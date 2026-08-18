"""
agent/clients/pod.py

PODAgent — agente especializado em POD Kombucha (Google Ads).

Diferencial vs BaseClientAgent:
  - System prompt específico POD (Google Ads)
  - PLATAFORMA_PADRAO = 'google_ads'
  - Sem enriquecimento Shopify (POD não tem Shopify integrado)
"""
from __future__ import annotations

import logging

from agent.clients.base import BaseClientAgent

logger = logging.getLogger(__name__)


class PODAgent(BaseClientAgent):
    """Agente POD Kombucha — Google Ads."""

    CLIENTE_KEY       = "pod_kombucha"
    PLATAFORMA_PADRAO = "google_ads"

    def _get_system_prompt(self) -> str:
        from clientes.pod.prompts import SYSTEM_PROMPT_POD
        return SYSTEM_PROMPT_POD
