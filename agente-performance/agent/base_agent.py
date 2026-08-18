"""
agent/base_agent.py — Classe base para todos os agentes de análise.

Provê:
  - Helpers de validação de campanha
  - Sugestão de período quando dados estão vazios
  - Acesso à lista de campanhas disponíveis
  - Interface padrão: execute(intent, params) → Any
"""

import logging
from typing import Any, Optional

from agent import queries
from agent.nlp import fuzzy_match_campaign

logger = logging.getLogger(__name__)


class BaseAnalysisAgent:
    """
    Interface e utilitários comuns para agentes de análise.

    Subclasses devem implementar execute().
    """

    def execute(
        self,
        intent: str,
        campanha: Optional[str],
        data_inicio: Optional[str],
        data_fim: Optional[str],
        limit: int,
        meta: dict,
        cliente_key: Optional[str] = None,
        plataforma: Optional[str] = None,
    ) -> Any:
        raise NotImplementedError

    # ── Helpers compartilhados ────────────────────────────────────────────────

    def get_available_campaigns(
        self,
        cliente_key: Optional[str] = None,
        plataforma: Optional[str] = None,
    ) -> list[str]:
        """Retorna campanhas disponíveis, opcionalmente filtradas por cliente/plataforma."""
        try:
            _fk = {}
            if cliente_key:
                _fk["cliente_key"] = cliente_key
            if plataforma:
                _fk["plataforma"] = plataforma
            rows = queries.resumo_por_campanha(**_fk)
            if isinstance(rows, list):
                return [r["campanha"] for r in rows if r.get("campanha")]
        except Exception as e:
            logger.warning("[BaseAgent] get_available_campaigns: %s", e)
        return []

    def validate_campaign(
        self,
        campanha: Optional[str],
        available: list[str],
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Valida e normaliza nome de campanha via fuzzy match.

        Returns:
            (campanha_normalizada, erro_ou_None)
        """
        if campanha is None:
            return None, None

        match = fuzzy_match_campaign(campanha, available)
        if match:
            if match.lower() != campanha.lower():
                logger.debug(
                    "[BaseAgent] Fuzzy match: '%s' → '%s'", campanha, match
                )
            return match, None

        # Não encontrou — sugere alternativas
        sugestoes = available[:5]
        lista = "\n".join(f"  • {c}" for c in sugestoes)
        erro = (
            f"⚠️ Não encontrei campanha *{campanha}*.\n\n"
            f"*Campanhas disponíveis:*\n{lista}\n"
            f"...e mais {max(0, len(available) - 5)} outras.\n\n"
            f"_Tente parte do nome, ex: `Advantage` ou `ABO`_"
        )
        return None, erro

    def empty_suggestion(
        self,
        intent: str,
        campanha: Optional[str],
        data_inicio: Optional[str],
        data_fim: Optional[str],
    ) -> Optional[str]:
        """
        Sugestão de período alternativo quando dados estão vazios.
        Busca o intervalo mais recente com dados no banco.
        """
        try:
            from db.connection import query as raw_query
            sem_filtro = raw_query(
                "SELECT MIN(data_inicio) AS ini, MAX(data_fim) AS fim, COUNT(*) AS n "
                "FROM campanhas_unificada"
            )
            if sem_filtro and sem_filtro[0].get("n", 0) > 0:
                ini = sem_filtro[0]["ini"]
                fim = sem_filtro[0]["fim"]
                return (
                    f"⚠️ Nenhum dado encontrado para o período solicitado.\n\n"
                    f"_Dados disponíveis: {ini} → {fim}_\n"
                    f"Tente: `resumo de {ini} a {fim}`"
                )
        except Exception as e:
            logger.debug("[BaseAgent] empty_suggestion: %s", e)
        return "⚠️ Nenhum dado encontrado para o período solicitado."
