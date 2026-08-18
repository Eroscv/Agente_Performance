"""
agent/clients/source_of.py

SourceOFAgent — agente especializado em Source OF.

⚠️ REGRA PERMANENTE:
  Source OF tem: Meta Ads ✅ + Google Ads ✅ + Shopify ✅
  É o único cliente com Shopify integrado.

Diferencial vs BaseClientAgent:
  - System prompt específico Source OF (Meta Ads + Shopify)
  - PLATAFORMA_PADRAO = 'meta_ads'
  - Enriquecimento Shopify automático: injeta receita real, ROAS real,
    CAC e taxa de recompra quando dados sincronizados disponíveis
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from agent.clients.base import BaseClientAgent

logger = logging.getLogger(__name__)


class SourceOFAgent(BaseClientAgent):
    """Agente Source OF — Meta Ads + Google Ads + Shopify."""

    CLIENTE_KEY       = "source_of"
    PLATAFORMA_PADRAO = "meta_ads"

    @property
    def relatorio(self):
        """Subagente de relatório da Source OF."""
        from agent.clients.relatorio import get_relatorio_agent
        return get_relatorio_agent(self.CLIENTE_KEY)

    def _get_system_prompt(self) -> str:
        from clientes.source_of.prompts import SYSTEM_PROMPT
        return SYSTEM_PROMPT

    def _enriquecer(self, params: dict, dados: Any, meta: dict) -> dict:
        """
        Enriquecimento Shopify: injeta receita real quando sincronizado.
        Só roda se o intent for de performance/resumo (não ranking puro).
        Source OF é o ÚNICO cliente com Shopify — nunca chamar para POD.
        """
        intent = params.get("intent", "")
        _INTENTS_SHOPIFY = {
            "resumo_geral", "periodo", "resumo_executivo",
            "performance_campanha", "projecao_mes", "historico_mensal",
        }
        if intent not in _INTENTS_SHOPIFY:
            return {}

        try:
            from agent.queries.shopify import receita_shopify_periodo
            data_inicio = params.get("data_inicio")
            data_fim    = params.get("data_fim")
            shopify = receita_shopify_periodo(data_inicio, data_fim)
            if shopify and shopify.get("sincronizado"):
                return {"shopify": shopify}
        except Exception as e:
            logger.debug("[SourceOFAgent] shopify enrich: %s", e)

        return {}
