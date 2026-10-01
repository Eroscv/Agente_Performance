"""
agent/clients/botica.py

BoticaAgent — agente especializado em Botica Ouro Preto.

Plataformas ativas: Meta Ads ✅ | Shopify ✅
Google Ads: não configurado ainda.
"""
from __future__ import annotations

import logging

from agent.clients.base import BaseClientAgent

logger = logging.getLogger(__name__)


class BoticaAgent(BaseClientAgent):
    """Agente Botica Ouro Preto — Meta Ads (principal)."""

    CLIENTE_KEY       = "botica"
    PLATAFORMA_PADRAO = "meta_ads"

    def _get_system_prompt(self) -> str:
        from clientes.botica.prompts import SYSTEM_PROMPT_BOTICA_META
        return SYSTEM_PROMPT_BOTICA_META
