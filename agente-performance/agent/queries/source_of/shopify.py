"""
agent/queries/source_of/shopify.py

Queries específicas Source OF · Shopify.
Source OF é o único cliente com integração Shopify.

Atalhos para as funções de agent/queries/shopify.py com contexto pré-fixado.
"""
from __future__ import annotations
from typing import Optional


def receita_periodo(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    campanha:    Optional[str] = None,
) -> dict:
    """
    Receita Shopify + dados Meta Ads do período — Source OF.
    Enriquece investimento Meta com receita real Shopify para ROAS real.
    """
    from agent.queries.shopify import receita_shopify_periodo
    return receita_shopify_periodo(
        data_inicio, data_fim, campanha, cliente_key="source_of"
    )


def diario(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Métricas Shopify diárias — Source OF."""
    from agent.queries.shopify import metricas_shopify_diario
    return metricas_shopify_diario(data_inicio, data_fim)


def status() -> dict:
    """Status da sincronização Shopify — Source OF."""
    from agent.queries.shopify import status_shopify_sync
    return status_shopify_sync()
