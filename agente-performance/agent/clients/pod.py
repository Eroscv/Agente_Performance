"""
agent/clients/pod.py

PODAgent — agente especializado em POD Kombucha.

⚠️ REGRA PERMANENTE:
  POD Kombucha tem: Meta Ads ✅ + Google Ads ✅ (ativo=false no clientes.json)
  POD NÃO tem Shopify — nunca injetar dados Shopify em análises da POD.

Diferencial vs BaseClientAgent:
  - System prompt específico POD
  - PLATAFORMA_PADRAO = 'meta_ads' (Meta Ads é a plataforma ativa)
  - Sem enriquecimento Shopify
"""
from __future__ import annotations

import logging

from agent.clients.base import BaseClientAgent

logger = logging.getLogger(__name__)


class PODAgent(BaseClientAgent):
    """Agente POD Kombucha — Meta Ads (principal) + Google Ads."""

    CLIENTE_KEY       = "pod_kombucha"
    PLATAFORMA_PADRAO = "meta_ads"  # Meta Ads é a plataforma ativa da POD

    @property
    def relatorio(self):
        """Subagente de relatório da POD Kombucha."""
        from agent.clients.relatorio import get_relatorio_agent
        return get_relatorio_agent(self.CLIENTE_KEY)

    def _get_system_prompt(self) -> str:
        from clientes.pod.prompts import SYSTEM_PROMPT_POD
        return SYSTEM_PROMPT_POD
