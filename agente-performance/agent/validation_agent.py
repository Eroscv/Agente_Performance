"""
agent/validation_agent.py — Validação de sanidade dos dados antes do LLM.

Responsabilidade única: receber dados brutos das queries e verificar se
fazem sentido antes de serem enviados ao Claude.

Não faz queries. Não chama LLM. Apenas aplica regras puras.

Regras de negócio:
  - FATOR_IMPOSTO = 1.1215 (Bianca confirmou)
  - ROAS Meta bruto nunca deve ser usado — só ROAS Shopify ajustado
  - Frequência > 4 = fadiga (Meta Ads)
  - CTR > 15% em Meta = dado suspeito
  - Divergência ROAS Meta vs Shopify > 40% = alerta
  - Período com < 50% dos dias esperados = cobertura incompleta

Integração:
  ValidationResult é retornado por validate() e contém:
    - avisos: list[str]   — injetados no user_message do Claude
    - erros:  list[str]   — dados impossíveis (investimento negativo, etc.)
    - meta_extra: dict    — campos corrigidos/recalculados (ex: roas_real)

Uso no OrchestratorAgent:
    dados, vr = ValidationAgent().validate(dados, intent, meta, plataforma)
    # vr.avisos → injetados no prompt
    # vr.erros  → logados + incluídos no aviso_vazio se críticos
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Regras de negócio (fonte: relatorio/regras.py + reunião Bianca/Eros) ──────
FATOR_IMPOSTO: float = 1.1215   # investimento Meta ajustado

# Benchmarks Meta Ads (Source OF)
_META_CTR_MAX        = 15.0    # CTR acima disso é suspeito
_META_FREQUENCIA_MAX = 4.0     # fadiga de audiência
_META_ROAS_MIN       = 0.1     # ROAS abaixo = campanha claramente no prejuízo
_META_ROAS_MAX       = 50.0    # ROAS acima = dado suspeito (possível erro de atribuição)
_ROAS_DIVERGENCIA    = 0.40    # 40% de divergência Meta vs Shopify = alerta

# Benchmarks Google Ads (POD Kombucha)
_GADS_CTR_MAX        = 35.0    # Search pode ter CTR alto
_GADS_ROAS_MAX       = 100.0   # PMAX pode ter ROAS alto

# Cobertura mínima de período (50% dos dias esperados)
_COBERTURA_MIN_FRAC  = 0.50


@dataclass
class ValidationResult:
    """
    Resultado da validação de um conjunto de dados.

    avisos:    alertas contextuais — injetados no prompt como contexto
    erros:     dados impossíveis — logados e exibidos como aviso crítico
    meta_extra: campos recalculados (ex: roas_real recalculado com FATOR_IMPOSTO)
    """
    avisos:     list[str] = field(default_factory=list)
    erros:      list[str] = field(default_factory=list)
    meta_extra: dict      = field(default_factory=dict)

    def ok(self) -> bool:
        return not self.erros

    def tem_avisos(self) -> bool:
        return bool(self.avisos)

    def para_prompt(self) -> str:
        """
        Formata avisos e erros para injeção no user_message do Claude.
        Retorna string vazia se não há nada a reportar.
        """
        linhas = []
        if self.erros:
            linhas.append("[VALIDAÇÃO — ERROS NOS DADOS]")
            for e in self.erros:
                linhas.append(f"  ❌ {e}")
        if self.avisos:
            linhas.append("[VALIDAÇÃO — ALERTAS DE QUALIDADE]")
            for a in self.avisos:
                linhas.append(f"  {a}")
        return "\n".join(linhas)


class ValidationAgent:
    """
    Valida sanidade dos dados retornados pelas queries.

    Uso:
        vr = ValidationAgent().validate(dados, intent, meta, plataforma)
        # vr.para_prompt() → injeta no user_message
        # vr.erros         → dados impossíveis
    """

    def validate(
        self,
        dados: Any,
        intent: str,
        meta: dict,
        plataforma: Optional[str] = None,
    ) -> ValidationResult:
        """
        Ponto de entrada principal.

        Args:
            dados:      Resultado bruto da query (list[dict] ou dict)
            intent:     Intent da consulta
            meta:       Metadados da consulta (datas, cliente_key, etc.)
            plataforma: 'meta_ads' | 'google_ads' | None

        Returns:
            ValidationResult com avisos, erros e meta_extra
        """
        vr = ValidationResult()

        if dados is None:
            return vr

        plat = (plataforma or meta.get("plataforma") or "meta_ads").lower()
        is_meta   = "meta" in plat
        is_google = "google" in plat

        # Normaliza para lista de rows
        rows = dados if isinstance(dados, list) else [dados]
        rows = [r for r in rows if isinstance(r, dict)]

        if not rows:
            return vr

        # ── 1. Cobertura do período ───────────────────────────────────────────
        self._check_cobertura(vr, rows, meta)

        # ── 2. Métricas impossíveis (erros) ──────────────────────────────────
        self._check_impossiveis(vr, rows)

        # ── 3. Benchmarks por plataforma ─────────────────────────────────────
        if is_meta:
            self._check_meta(vr, rows, meta)
        elif is_google:
            self._check_google(vr, rows)

        # ── 4. Divergência ROAS Meta vs Shopify ──────────────────────────────
        if is_meta and isinstance(dados, dict):
            self._check_roas_divergencia(vr, dados)

        # ── 5. Recalcula ROAS real com FATOR_IMPOSTO (Meta) ──────────────────
        if is_meta and isinstance(dados, dict):
            self._recalcular_roas_meta(vr, dados)

        logger.debug(
            "[ValidationAgent] plat=%s | erros=%d | avisos=%d",
            plat, len(vr.erros), len(vr.avisos),
        )
        return vr

    # ── Checagens ─────────────────────────────────────────────────────────────

    def _check_cobertura(
        self,
        vr: ValidationResult,
        rows: list[dict],
        meta: dict,
    ) -> None:
        """Verifica se o período retornado cobre o período solicitado."""
        data_inicio = meta.get("data_inicio")
        data_fim    = meta.get("data_fim")
        if not (data_inicio and data_fim):
            return

        try:
            ini = date.fromisoformat(data_inicio)
            fim = date.fromisoformat(data_fim)
            dias_esperados = (fim - ini).days + 1

            # Conta dias únicos com dados
            datas_com_dados: set[str] = set()
            for r in rows:
                d_ini = r.get("data_inicio") or r.get("dia") or r.get("data")
                d_fim = r.get("data_fim")
                if d_ini:
                    datas_com_dados.add(str(d_ini)[:10])
                if d_fim:
                    datas_com_dados.add(str(d_fim)[:10])

            # Só avisa se houver dados de datas individuais
            if len(datas_com_dados) > 0:
                cobertura = len(datas_com_dados) / dias_esperados
                if cobertura < _COBERTURA_MIN_FRAC and dias_esperados > 3:
                    vr.avisos.append(
                        f"⚠️ Cobertura de dados: {len(datas_com_dados)}/{dias_esperados} dias "
                        f"({cobertura*100:.0f}%) — período pode estar incompleto"
                    )
        except (ValueError, TypeError):
            pass

    def _check_impossiveis(
        self,
        vr: ValidationResult,
        rows: list[dict],
    ) -> None:
        """Detecta valores matematicamente impossíveis."""
        for i, r in enumerate(rows[:10]):  # máx 10 linhas para não poluir
            camp = r.get("campanha", f"linha {i+1}")

            invest = r.get("investimento") or r.get("investimento_total")
            if invest is not None and invest < 0:
                vr.erros.append(f"Investimento negativo em '{camp}': R$ {invest:.2f}")

            compras = r.get("compras") or r.get("total_compras") or r.get("compras_total")
            if compras is not None and compras < 0:
                vr.erros.append(f"Compras negativas em '{camp}': {compras}")

            impressoes = r.get("impressoes")
            cliques    = r.get("cliques_link") or r.get("total_cliques")
            if impressoes is not None and cliques is not None:
                if impressoes > 0 and cliques > impressoes:
                    vr.erros.append(
                        f"Cliques ({cliques}) maiores que impressões ({impressoes}) em '{camp}'"
                    )

            # Investimento zero com compras — dado de atribuição suspeito
            if invest == 0 and compras and compras > 10:
                vr.avisos.append(
                    f"⚠️ '{camp}': {compras} compras com investimento R$ 0 "
                    f"— possível erro de atribuição"
                )

    def _check_meta(
        self,
        vr: ValidationResult,
        rows: list[dict],
        meta: dict,
    ) -> None:
        """Regras específicas Meta Ads."""
        for r in rows:
            camp = r.get("campanha", "")

            # CTR suspeito
            ctr = r.get("ctr_link") or r.get("ctr") or r.get("ctr_percentual")
            if ctr is not None and ctr > _META_CTR_MAX:
                vr.avisos.append(
                    f"⚠️ CTR suspeito em '{camp}': {ctr:.1f}% "
                    f"(acima de {_META_CTR_MAX}% é improvável no Meta)"
                )

            # Fadiga de frequência
            freq = r.get("frequencia")
            if freq is not None and freq > _META_FREQUENCIA_MAX:
                vr.avisos.append(
                    f"⚠️ Fadiga de audiência em '{camp}': frequência {freq:.1f}x "
                    f"(acima de {_META_FREQUENCIA_MAX}x)"
                )

            # ROAS improvável
            roas = r.get("roas") or r.get("roas_compras") or r.get("roas_atual")
            if roas is not None:
                if roas > _META_ROAS_MAX:
                    vr.avisos.append(
                        f"⚠️ ROAS suspeito em '{camp}': {roas:.1f}x "
                        f"(acima de {_META_ROAS_MAX}x — possível erro de atribuição)"
                    )
                elif 0 < roas < _META_ROAS_MIN:
                    vr.avisos.append(
                        f"⚠️ ROAS muito baixo em '{camp}': {roas:.2f}x — campanha no prejuízo"
                    )

    def _check_google(
        self,
        vr: ValidationResult,
        rows: list[dict],
    ) -> None:
        """Regras específicas Google Ads."""
        for r in rows:
            camp = r.get("campanha", "")

            ctr = r.get("ctr_link") or r.get("ctr")
            if ctr is not None and ctr > _GADS_CTR_MAX:
                vr.avisos.append(
                    f"⚠️ CTR muito alto em '{camp}': {ctr:.1f}% "
                    f"(acima de {_GADS_CTR_MAX}% — verificar tipo de campanha)"
                )

            roas = r.get("roas") or r.get("roas_compras") or r.get("roas_atual")
            if roas is not None and roas > _GADS_ROAS_MAX:
                vr.avisos.append(
                    f"⚠️ ROAS muito alto em '{camp}': {roas:.1f}x — verificar atribuição"
                )

    def _check_roas_divergencia(
        self,
        vr: ValidationResult,
        dados: dict,
    ) -> None:
        """Alerta quando ROAS Meta diverge muito do ROAS Shopify real."""
        roas_meta    = dados.get("roas_compras") or dados.get("roas")
        roas_shopify = dados.get("roas_shopify")
        if not (roas_meta and roas_shopify and roas_meta > 0):
            return

        divergencia = abs(roas_meta - roas_shopify) / roas_meta
        if divergencia > _ROAS_DIVERGENCIA:
            vr.avisos.append(
                f"⚠️ Divergência ROAS: Meta reporta {roas_meta:.2f}x vs "
                f"Shopify real {roas_shopify:.2f}x "
                f"({divergencia*100:.0f}% de diferença) — "
                f"use o ROAS Shopify como referência"
            )

    def _recalcular_roas_meta(
        self,
        vr: ValidationResult,
        dados: dict,
    ) -> None:
        """
        Recalcula ROAS com FATOR_IMPOSTO quando Shopify não está disponível.
        Injeta em meta_extra para o Claude usar se necessário.
        """
        roas_shopify = dados.get("roas_shopify")
        if roas_shopify:
            return  # Shopify disponível — não precisa recalcular

        invest = dados.get("investimento") or dados.get("investimento_total")
        receita = dados.get("valor_compras") or dados.get("receita_total")
        if invest and receita and invest > 0:
            invest_ajustado = round(invest * FATOR_IMPOSTO, 2)
            roas_ajustado   = round(receita / invest_ajustado, 4)
            vr.meta_extra["roas_ajustado_imposto"] = roas_ajustado
            vr.meta_extra["investimento_ajustado"] = invest_ajustado
            logger.debug(
                "[ValidationAgent] ROAS recalculado com imposto: %.4f "
                "(invest_ajustado=%.2f, receita=%.2f)",
                roas_ajustado, invest_ajustado, receita,
            )
