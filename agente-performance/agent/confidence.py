"""
agent/confidence.py — Confidence / Evidence para respostas do agente.

Calcula o nível de confiança de uma resposta com base em:
  - Cobertura de dados (quantos dias têm dados vs período solicitado)
  - Volume de dados (linhas retornadas, investimento mínimo)
  - Qualidade da fonte (Shopify disponível = maior confiança)
  - Erros de validação (ValidationAgent encontrou problemas)
  - Tipo de intent (SQL dinâmico = menor confiança que intent fixo)

Retorna um ConfidenceResult injetado no resultado do engine e no prompt
do Claude, para que ele cite a evidência e calibre o tom da resposta.

Schema do resultado:
{
  "level":    "high" | "medium" | "low",
  "score":    float,          # 0.0–1.0
  "reason":   str | None,     # explicação quando low/medium
  "evidence": list[str],      # query ids / fontes usadas
  "warnings": list[str],      # itens que reduziram a confiança
}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
_HIGH_THRESHOLD   = 0.80
_MEDIUM_THRESHOLD = 0.45

_MIN_INVEST_HIGH   = 1_000.0   # R$ mínimo para confiança alta
_MIN_INVEST_MEDIUM = 100.0     # R$ mínimo para confiança média
_MIN_ROWS_LIST     = 3         # mín de linhas para list[dict] ser confiável
_MIN_DIAS_COBERTURA = 0.70     # 70% dos dias com dados → confiança não penalizada


@dataclass
class ConfidenceResult:
    level:    str        # "high" | "medium" | "low"
    score:    float      # 0.0–1.0
    reason:   Optional[str]       = None
    evidence: list[str]           = field(default_factory=list)
    warnings: list[str]           = field(default_factory=list)

    def para_prompt(self) -> str:
        """
        Bloco injetado no user_message.
        Instrui o Claude a calibrar o tom conforme a confiança.
        """
        nivel_pt = {"high": "alta", "medium": "média", "low": "baixa"}.get(self.level, self.level)
        linhas = [
            f"[CONFIANÇA DA RESPOSTA: {self.level.upper()} ({nivel_pt}, score={self.score:.2f})]",
        ]
        if self.evidence:
            linhas.append(f"  Evidência: {', '.join(self.evidence)}")
        if self.reason:
            linhas.append(f"  Motivo: {self.reason}")
        if self.warnings:
            for w in self.warnings:
                linhas.append(f"  ⚠️ {w}")

        if self.level == "high":
            linhas.append(
                "  → Responda com assertividade. Os dados são suficientes e confiáveis."
            )
        elif self.level == "medium":
            linhas.append(
                "  → Responda com ressalvas. Mencione limitações dos dados quando relevante."
            )
        else:
            linhas.append(
                "  → Deixe CLARO que os dados são insuficientes para uma conclusão confiável. "
                "Não afirme tendências. Indique o que falta."
            )
        return "\n".join(linhas)

    def to_dict(self) -> dict:
        return {
            "level":    self.level,
            "score":    round(self.score, 3),
            "reason":   self.reason,
            "evidence": self.evidence,
            "warnings": self.warnings,
        }


# ── Motor de cálculo ──────────────────────────────────────────────────────────

def calcular_confianca(
    dados: Any,
    intent: str,
    meta: dict,
    delta: dict,
    shopify: dict,
    validacao_erros: list[str],
    validacao_avisos: list[str],
) -> ConfidenceResult:
    """
    Calcula o nível de confiança da resposta.

    Pontuação base: 1.0
    Penalidades aplicadas progressivamente.
    """
    score    = 1.0
    warnings = []
    evidence = []
    reason   = None

    # ── Evidências (fontes usadas) ────────────────────────────────────────────
    if intent:
        evidence.append(f"intent:{intent}")

    data_inicio = meta.get("data_inicio")
    data_fim    = meta.get("data_fim")
    if data_inicio and data_fim:
        evidence.append(f"periodo:{data_inicio}→{data_fim}")

    if shopify.get("sincronizado"):
        evidence.append("shopify:sincronizado")
    
    if meta.get("sql_dinamico"):
        evidence.append("sql:dinamico")

    # ── Penalidade 1: dados vazios ou None ────────────────────────────────────
    dados_vazios = (
        dados is None
        or (isinstance(dados, list) and len(dados) == 0)
        or (isinstance(dados, dict) and not dados)
    )
    if dados_vazios:
        score = 0.0
        return ConfidenceResult(
            level="low", score=0.0,
            reason="Nenhum dado encontrado para o período solicitado.",
            evidence=evidence,
        )

    # ── Penalidade 2: erros de validação (dados impossíveis) ──────────────────
    if validacao_erros:
        score -= 0.35
        warnings.append(f"Dados com erros de validação: {'; '.join(validacao_erros[:2])}")

    # ── Penalidade 3: volume insuficiente ─────────────────────────────────────
    if isinstance(dados, list):
        n = len(dados)
        if n < _MIN_ROWS_LIST:
            score -= 0.20
            warnings.append(f"Apenas {n} registro(s) retornados — amostra pequena")
            if n == 1:
                reason = reason or "Apenas 1 registro — não é possível identificar tendências."

    # Investimento mínimo (para dados dict)
    if isinstance(dados, dict):
        invest = (
            dados.get("investimento")
            or dados.get("investimento_total")
            or dados.get("investimento_semana_atual")
            or 0
        )
        if invest > 0:
            if invest < _MIN_INVEST_MEDIUM:
                score -= 0.35
                warnings.append(
                    f"Investimento muito baixo: R$ {invest:.0f} "
                    f"— dados podem ser inconclusivos"
                )
            elif invest < _MIN_INVEST_HIGH:
                score -= 0.15
                warnings.append(
                    f"Investimento baixo: R$ {invest:.0f} "
                    f"— conclusões com ressalva"
                )

    # ── Penalidade 4: cobertura do período ────────────────────────────────────
    if data_inicio and data_fim:
        try:
            ini = date.fromisoformat(data_inicio)
            fim = date.fromisoformat(data_fim)
            dias_esperados = (fim - ini).days + 1

            rows = dados if isinstance(dados, list) else [dados]
            datas: set[str] = set()
            for r in rows:
                if not isinstance(r, dict):
                    continue
                for campo in ("data_inicio", "dia", "data"):
                    v = r.get(campo)
                    if v:
                        datas.add(str(v)[:10])
                        break

            if datas and dias_esperados > 3:
                cobertura = len(datas) / dias_esperados
                if cobertura < _MIN_DIAS_COBERTURA:
                    penalidade = round((1 - cobertura) * 0.30, 2)
                    score -= penalidade
                    warnings.append(
                        f"Cobertura de {cobertura*100:.0f}% do período "
                        f"({len(datas)}/{dias_esperados} dias)"
                    )
                    if cobertura < 0.30:
                        reason = reason or (
                            f"Dados de apenas {len(datas)} dias num período de "
                            f"{dias_esperados} dias — não é suficiente para comparar."
                        )
        except (ValueError, TypeError):
            pass

    # ── Penalidade 5: SQL dinâmico (menos confiável que intent fixo) ──────────
    if meta.get("sql_dinamico"):
        score -= 0.10
        warnings.append("Resposta gerada via SQL dinâmico — query ad-hoc, não testada")

    # ── Penalidade 6: avisos de validação (não erros, mas reduzem confiança) ──
    if validacao_avisos:
        score -= min(0.10 * len(validacao_avisos), 0.25)
        # Não adiciona aos warnings para não duplicar (já estarão no validacao_str)

    # ── Bônus: Shopify sincronizado (fonte mais confiável que atribuição Meta) ─
    if shopify.get("sincronizado") and shopify.get("roas_real"):
        score = min(score + 0.10, 1.0)

    # ── Bônus: delta disponível (comparativo enriquece a resposta) ─────────────
    if delta:
        score = min(score + 0.05, 1.0)
        evidence.append(f"delta:{len(delta)} métricas comparadas")

    # ── Clamp e nível ─────────────────────────────────────────────────────────
    score = max(0.0, min(score, 1.0))

    if score >= _HIGH_THRESHOLD:
        level = "high"
    elif score >= _MEDIUM_THRESHOLD:
        level = "medium"
    else:
        level = "low"
        if not reason:
            reason = reason or (
                "Dados insuficientes ou com problemas de qualidade para "
                "uma conclusão confiável."
            )

    logger.debug(
        "[confidence] intent=%s level=%s score=%.2f warnings=%d",
        intent, level, score, len(warnings),
    )

    return ConfidenceResult(
        level=level,
        score=score,
        reason=reason,
        evidence=evidence,
        warnings=warnings,
    )
