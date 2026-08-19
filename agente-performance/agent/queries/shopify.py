"""
agent/queries/shopify.py

Receita Shopify sincronizada, métricas diárias e status de sync.
"""
import logging
from typing import Optional

from db.connections import query_fed as query, query_fed_one as query_one, LEGACY_DB_PATH as DB_PATH
from utils.formatters import fmt_brl

logger = logging.getLogger(__name__)

from agent.queries.base import _build_where, _meta_ads_union_source, _meta_ads_where

def receita_shopify_periodo(
    data_inicio: str,
    data_fim: str,
) -> dict:
    """
    Retorna métricas Shopify sincronizadas para o período.

    Usa dados da tabela `shopify_receita_diaria` (sem bater na API Shopify).
    Inclui ROAS real (Receita Shopify / Investimento Meta ajustado).

    Returns:
        {
            receita_total, total_pedidos, ticket_medio,
            novos_clientes, recompradores, taxa_recompra_pct,
            total_desconto, pedidos_acima_300,
            roas_real, investimento_meta_ajustado,
            sincronizado: bool,
        }
    """
    from datetime import date as _date
    try:
        from db.shopify_sync import metricas_shopify_periodo as _ms
        d0 = _date.fromisoformat(data_inicio)
        d1 = _date.fromisoformat(data_fim)
        shopify = _ms(d0, d1)
    except Exception as e:
        logger.warning("[queries] receita_shopify_periodo: erro ao buscar dados — %s", e)
        return {"sincronizado": False, "erro": str(e)}

    if not shopify.get("sincronizado"):
        return shopify  # retorna {"sincronizado": False, ...}

    # Busca investimento Meta no mesmo período
    inv_row = query_one(
        "SELECT ROUND(SUM(investimento), 2) AS investimento "
        "FROM campanhas_unificada WHERE data_inicio >= ? AND data_fim <= ?",
        (data_inicio, data_fim),
    )
    invest_meta = (inv_row or {}).get("investimento") or 0.0

    from relatorio.regras import spend_com_imposto, calcular_cac
    invest_ajustado = spend_com_imposto(invest_meta) if invest_meta else 0.0
    receita = shopify.get("receita_total") or 0.0

    roas_real = (
        round(receita / invest_ajustado, 4)
        if invest_ajustado and receita
        else None
    )
    cac = calcular_cac(invest_meta, shopify.get("novos_clientes") or 0) if invest_meta else None

    return {
        **shopify,
        "investimento_meta":          round(invest_meta, 2),
        "investimento_meta_ajustado": round(invest_ajustado, 2),
        "roas_real":                  roas_real,
        "cac":                        cac,
    }



def metricas_shopify_diario(
    data_inicio: str,
    data_fim: str,
) -> list[dict]:
    """
    Receita Shopify por dia no período (para evolução diária).

    Returns:
        [{"data": "YYYY-MM-DD", "receita": float, "pedidos": int, ...}]
    """
    from db.connections import query_fed

    try:
        rows = query_fed(
            """
            SELECT
                data,
                receita,
                pedidos,
                novos_clientes,
                recompradores,
                ticket_medio,
                total_desconto,
                pedidos_acima_300
            FROM shopify_receita_diaria
            WHERE data BETWEEN ? AND ?
            ORDER BY data ASC
            """,
            (data_inicio, data_fim),
        )
        return rows
    except Exception as e:
        logger.warning("[queries] metricas_shopify_diario: %s", e)
        return []



def status_shopify_sync() -> dict:
    """Status da sincronização Shopify — para /shopify/status."""
    try:
        from db.shopify_sync import status_sync
        return status_sync()
    except Exception as e:
        return {"sincronizado": False, "erro": str(e)}


# ── Novas funções v4 — Melhorias de dados e consultas ────────────────────────


