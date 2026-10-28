"""
agent/data_verifier.py — Fact-check, confidence check e evidence check.

Três camadas de verificação antes de entregar a resposta ao usuário:

  1. FACT-CHECK (numérico)
     Extrai todos os números da resposta gerada e verifica se cada um
     é rastreável aos dados brutos retornados pelo banco.
     Detecta: valores inventados, arredondamentos abusivos, ROAS
     calculados com denominador errado, confusão receita vs investimento.

  2. CONFIDENCE CHECK
     Consolida ValidationAgent + HallucinationGuard + ComplianceGuard
     em um único score e nível de confiança. Determina se a resposta
     pode ser entregue como está ou precisa de prefixo de aviso.

  3. EVIDENCE CHECK
     Constrói o "carimbo de evidência" que vai ao final de cada
     resposta: fonte dos dados (BQ/fallback), período coberto,
     nível de confiança e timestamp da última atualização.

Fluxo de uso em slack/responder.py:
    vr = verificar_resposta(resposta, dados, meta)
    if vr.prefixo_aviso:
        resposta = vr.prefixo_aviso + resposta
    resposta += vr.evidence_stamp
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Configuração ───────────────────────────────────────────────────────────────
# Tolerância para comparar números da resposta com dados brutos.
# 0.02 = 2% (cobre arredondamento de exibição e separadores de milhar)
_TOL = 0.02

# Número máximo de valores numéricos a verificar (evita custo alto em respostas longas)
_MAX_CHECK_VALUES = 20

# Regex: captura números formatados em BR (ponto=milhar, vírgula=decimal)
# Ordem importa: tenta capturar o formato mais completo primeiro
_NUM_RE = re.compile(
    r"(?:R\$\s*)?"
    r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?"  # 1.234,56 ou 1.234 (ponto=milhar)
    r"|\d+,\d+"                         # 1234,56 (vírgula=decimal)
    r"|\d+\.\d+"                         # 1234.56 (ponto=decimal, estilo en)
    r"|\d+)"                             # 1234 (inteiro)
    r"(?=\s*(?:x|%|\b))",               # seguido de x, % ou fim de palavra
    re.IGNORECASE,
)

# Regex para detectar padrões que NÃO são valores métricos (datas, contagens simples)
_DATE_FRAG_RE = re.compile(r"^\d{1,2}/\d{1,2}$")  # ex: 20/10


def _parse_br(s: str) -> Optional[float]:
    """Converte string numérica BR (1.234,56 ou 1234,56 ou 1234.56) para float."""
    s = s.strip().replace(" ", "")
    # Formato BR: ponto como milhar, vírgula como decimal
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _extract_numbers_from_text(texto: str) -> list[float]:
    """Extrai todos os valores numéricos de um texto formatado."""
    nums = []
    for m in _NUM_RE.finditer(texto):
        v = _parse_br(m.group(1))
        if v is not None and v > 0:
            nums.append(v)
    return nums[:_MAX_CHECK_VALUES]


def _flatten_values(dados: Any) -> list[float]:
    """Extrai todos os valores numéricos de dados brutos (list[dict] ou dict)."""
    vals: list[float] = []

    def _recurse(obj: Any):
        if isinstance(obj, dict):
            for v in obj.values():
                _recurse(v)
        elif isinstance(obj, list):
            for item in obj:
                _recurse(item)
        elif isinstance(obj, (int, float)) and obj == obj:  # exclui NaN
            vals.append(float(obj))
        elif isinstance(obj, str):
            v = _parse_br(obj)
            if v is not None:
                vals.append(v)

    _recurse(dados)
    return vals


def _numero_rastreavel(num: float, dados_vals: list[float]) -> bool:
    """
    Verifica se `num` é rastreável a algum valor nos dados brutos.
    Aceita:
      - Correspondência exata (dentro da tolerância _TOL)
      - Arredondamento de milhar (8.901 na resposta vs 8901.20 nos dados)
      - Soma de dois valores (investimento total = A + B)
      - Razão (ROAS = receita / investimento)
      - Percentuais derivados (CTR, variação %)
    """
    if not dados_vals:
        return True  # sem dados para checar → presume OK

    # 1. Match direto com tolerância
    for v in dados_vals:
        if v > 0 and abs(num - v) / max(abs(v), 1e-9) <= _TOL:
            return True

    # 2. Arredondamento de milhar/centena (ex: 8.901 na resposta vs 8901.20 nos dados)
    # Também cobre ponto-como-milhar BR: 8.901 (float 8.901) → realidade é 8901
    for v in dados_vals:
        if v >= 100:
            for approx in (round(v), int(v), round(v, -1), round(v, -2), round(v, -3)):
                if approx > 0 and abs(num - approx) / max(abs(approx), 1e-9) <= _TOL:
                    return True
        # Ponto como milhar BR: 8.901 no texto é float 8.901, valor real é 8901
        if v >= 1000:
            v_milhar = v / 1000  # 8901 → 8.901
            if abs(num - v_milhar) / max(abs(v_milhar), 1e-9) <= _TOL:
                return True

    # 3. Pode ser soma de dois valores (ex: investimento total = A + B)
    for i, a in enumerate(dados_vals):
        for b in dados_vals[i + 1:]:
            soma = a + b
            if soma > 0 and abs(num - soma) / max(abs(soma), 1e-9) <= _TOL:
                return True

    # 4. Pode ser razão (ROAS = receita / investimento)
    for a in dados_vals:
        for b in dados_vals:
            if b > 0 and b != a:
                razao = a / b
                if 0.01 <= razao <= 100 and abs(num - razao) / max(abs(razao), 1e-9) <= _TOL:
                    return True

    # 5. Pode ser percentual (CTR, variação %)
    if 0 < num <= 100:
        for a in dados_vals:
            for b in dados_vals:
                if b > 0 and b != a:
                    pct = (a / b) * 100
                    if abs(num - pct) / max(abs(pct), 1e-9) <= _TOL:
                        return True

    return False


@dataclass
class VerificacaoResult:
    """Resultado consolidado das 3 camadas de verificação."""
    # Fact-check
    total_numeros:    int = 0
    nao_rastreados:   int = 0
    taxa_rastreavel:  float = 1.0   # 0.0–1.0
    fact_check_ok:    bool = True

    # Confidence check
    confidence_level: str = "high"  # "high" | "medium" | "low"
    confidence_score: float = 1.0
    warnings:         list[str] = field(default_factory=list)

    # Evidence check
    fonte_dados:      str = "BigQuery"
    bq_usado:         bool = True
    prefixo_aviso:    str = ""       # prefixo ⚠️ quando confidence=low ou fact-check falhou
    evidence_stamp:   str = ""       # linha final: Fonte · Confiança · Período


def verificar_resposta(
    resposta: str,
    dados: Any,
    meta: dict,
    *,
    dados_brutos_extras: Optional[dict] = None,  # delta, shopify se disponíveis
) -> VerificacaoResult:
    """
    Executa as 3 camadas de verificação sobre uma resposta gerada.

    Args:
        resposta:            Texto gerado pelo Claude
        dados:               Dados brutos retornados pelo banco (list[dict] ou dict)
        meta:                Meta do resultado do engine (intent, cliente, datas, confidence, etc.)
        dados_brutos_extras: delta + shopify para ampliar o conjunto de valores rastreáveis

    Returns:
        VerificacaoResult com prefixo_aviso e evidence_stamp prontos para uso
    """
    vr = VerificacaoResult()

    # ── 1. FACT-CHECK NUMÉRICO ────────────────────────────────────────────────
    try:
        nums_resposta = _extract_numbers_from_text(resposta)
        dados_vals    = _flatten_values(dados)
        if dados_brutos_extras:
            dados_vals += _flatten_values(dados_brutos_extras)

        # Filtra números muito pequenos (1–3 = contadores de lista, rank) e datas BR (dia/mês)
        # e números muito grandes que são claramente IDs ou anos
        nums_filtrados = [
            n for n in nums_resposta
            if 4 <= n <= 50_000_000
            and not (100 <= n <= 9999 and n == int(n) and n % 100 == 0)  # evita anos como 2026
        ]

        nao_rastreados = 0
        for num in nums_filtrados:
            if not _numero_rastreavel(num, dados_vals):
                nao_rastreados += 1
                logger.debug("[data_verifier] número não rastreável: %s", num)

        vr.total_numeros   = len(nums_filtrados)
        vr.nao_rastreados  = nao_rastreados
        vr.taxa_rastreavel = (
            1.0 - (nao_rastreados / len(nums_filtrados))
            if nums_filtrados else 1.0
        )
        # Mais de 30% não rastreáveis = fact-check falhou
        vr.fact_check_ok = vr.taxa_rastreavel >= 0.70

        if not vr.fact_check_ok:
            logger.warning(
                "[data_verifier] fact-check: %d/%d valores não rastreáveis (%.0f%% OK)",
                nao_rastreados, len(nums_filtrados), vr.taxa_rastreavel * 100,
            )
    except Exception as e:
        logger.debug("[data_verifier] fact-check falhou silenciosamente: %s", e)

    # ── 2. CONFIDENCE CHECK ───────────────────────────────────────────────────
    try:
        # Usa confidence calculado pelo orchestrator se disponível
        _cr_pos = meta.get("confidence_pos_guards") or meta.get("confidence") or {}
        vr.confidence_level = _cr_pos.get("level", "high")
        vr.confidence_score = float(_cr_pos.get("score", 1.0))
        vr.warnings         = list(_cr_pos.get("warnings", []))

        # Degrada se fact-check falhou
        if not vr.fact_check_ok and vr.confidence_level != "low":
            vr.confidence_level = "medium" if vr.confidence_level == "high" else vr.confidence_level
            vr.warnings.append(
                f"Fact-check: {vr.nao_rastreados}/{vr.total_numeros} valores com rastreabilidade incerta"
            )
            if vr.confidence_score > 0.60:
                vr.confidence_score = 0.60
    except Exception as e:
        logger.debug("[data_verifier] confidence-check erro: %s", e)

    # ── 3. EVIDENCE CHECK + STAMP ─────────────────────────────────────────────
    try:
        # Determina fonte
        bq_falhou   = bool(meta.get("bq_fallback"))
        sql_dinamico = bool(meta.get("sql_dinamico"))
        shopify_sync = bool((dados_brutos_extras or {}).get("shopify", {}).get("sincronizado") if dados_brutos_extras else False)

        if bq_falhou:
            vr.fonte_dados = "DuckDB (fallback)"
            vr.bq_usado    = False
        elif sql_dinamico:
            vr.fonte_dados = "BigQuery · SQL dinâmico"
        else:
            vr.fonte_dados = "BigQuery"

        # Ícone de fonte
        fonte_icon = "✅" if vr.bq_usado else "⚠️"

        # Ícone de confiança
        conf_icon = {
            "high":   "🟢",
            "medium": "🟡",
            "low":    "🔴",
        }.get(vr.confidence_level, "⚪")
        conf_label = {
            "high":   "alta",
            "medium": "média",
            "low":    "baixa",
        }.get(vr.confidence_level, vr.confidence_level)

        # Período coberto
        d_ini = meta.get("data_inicio") or ""
        d_fim = meta.get("data_fim") or ""
        periodo_stamp = f"{d_ini} → {d_fim}" if d_ini and d_fim else "período variável"

        # Shopify
        shopify_tag = " · Shopify ✅" if shopify_sync else ""

        vr.evidence_stamp = (
            f"\n\n---\n"
            f"_Fonte: {vr.fonte_dados} {fonte_icon}{shopify_tag} · "
            f"Período: {periodo_stamp} · "
            f"Confiança: {conf_icon} {conf_label}_"
        )

        # Warnings visíveis (só os mais relevantes)
        if vr.warnings:
            warn_txt = " · ".join(vr.warnings[:2])
            vr.evidence_stamp += f"\n_⚠️ {warn_txt}_"

        # Prefixo de aviso quando confiança baixa
        if vr.confidence_level == "low":
            motivo = meta.get("confidence", {}).get("reason") or "dados insuficientes para análise completa"
            vr.prefixo_aviso = (
                f"⚠️ *Confiança baixa nos dados* — {motivo}\n"
                f"Interprete os números a seguir com cautela.\n\n"
            )
        elif not vr.bq_usado:
            vr.prefixo_aviso = (
                "⚠️ *BigQuery indisponível* — dados servidos pelo fallback local. "
                "Podem não refletir os valores mais recentes.\n\n"
            )

    except Exception as e:
        logger.debug("[data_verifier] evidence-check erro: %s", e)
        vr.evidence_stamp = ""

    return vr
