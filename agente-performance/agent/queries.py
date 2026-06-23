"""
agent/queries.py

Biblioteca de consultas SQL prontas para o agente.
Cada função retorna list[dict] com os dados solicitados.

Consultas disponíveis:
  - listar_campanhas()
  - resumo_por_campanha(data_inicio?, data_fim?)
  - performance_campanha(campanha, data_inicio?, data_fim?)
  - top_campanhas_roas(limit?, data_inicio?, data_fim?)
  - top_campanhas_compras(limit?, data_inicio?, data_fim?)
  - evolucao_diaria(campanha?, data_inicio?, data_fim?)
  - resumo_periodo(data_inicio, data_fim)
  - alertas_roas_baixo(threshold?)
  - comparar_campanhas(campanhas: list[str], data_inicio?, data_fim?)
  - metricas_funil(campanha?, data_inicio?, data_fim?)
"""

import logging
from typing import Optional
from db.connection import query, query_one

logger = logging.getLogger(__name__)


def listar_campanhas() -> list[dict]:
    """Lista todas as campanhas únicas com período de atividade."""
    return query("""
        SELECT
            campanha,
            MIN(data_inicio)  AS primeira_data,
            MAX(data_fim)      AS ultima_data,
            COUNT(*)           AS dias_com_dados,
            ROUND(SUM(investimento), 2) AS investimento_total
        FROM campanhas
        WHERE campanha IS NOT NULL AND campanha != ''
        GROUP BY campanha
        ORDER BY investimento_total DESC
    """)


def resumo_por_campanha(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Resumo agregado por campanha: investimento, compras, ROAS, CPC, CTR."""
    filters, params = _build_date_filter(data_inicio, data_fim)
    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(investimento), 2)          AS investimento,
            ROUND(SUM(valor_compras), 2)          AS receita,
            ROUND(SUM(compras), 0)                AS compras,
            ROUND(AVG(roas_compras), 2)           AS roas_medio,
            ROUND(SUM(impressoes), 0)             AS impressoes,
            ROUND(SUM(alcance), 0)                AS alcance,
            ROUND(SUM(cliques_link), 0)           AS cliques,
            ROUND(AVG(ctr_link), 4)               AS ctr_medio,
            ROUND(AVG(cpc_link), 2)               AS cpc_medio,
            ROUND(AVG(custo_por_compra), 2)       AS custo_por_compra,
            ROUND(AVG(frequencia), 2)             AS frequencia_media,
            COUNT(*)                              AS dias
        FROM campanhas
        WHERE campanha IS NOT NULL AND campanha != ''
        {filters}
        GROUP BY campanha
        ORDER BY investimento DESC
    """, tuple(params))


def performance_campanha(
    campanha:    str,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Série temporal diária de uma campanha específica."""
    filters, params = _build_date_filter(data_inicio, data_fim)
    params_full = [campanha] + params
    return query(f"""
        SELECT
            data_inicio                           AS data,
            ROUND(investimento, 2)                AS investimento,
            ROUND(valor_compras, 2)               AS receita,
            compras,
            ROUND(roas_compras, 2)                AS roas,
            impressoes,
            alcance,
            cliques_link,
            ROUND(ctr_link, 4)                    AS ctr,
            ROUND(cpc_link, 2)                    AS cpc,
            ROUND(custo_por_compra, 2)            AS custo_por_compra,
            ROUND(frequencia, 2)                  AS frequencia,
            adicoes_carrinho,
            checkout_iniciado
        FROM campanhas
        WHERE campanha = ?
        {filters}
        ORDER BY data_inicio ASC
    """, tuple(params_full))


def top_campanhas_roas(
    limit:       int = 10,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Top N campanhas por ROAS médio (mínimo R$100 investidos)."""
    filters, params = _build_date_filter(data_inicio, data_fim)
    params_full = params + [limit]
    return query(f"""
        SELECT
            campanha,
            ROUND(AVG(roas_compras), 2)     AS roas_medio,
            ROUND(SUM(investimento), 2)     AS investimento,
            ROUND(SUM(valor_compras), 2)    AS receita,
            ROUND(SUM(compras), 0)          AS compras
        FROM campanhas
        WHERE campanha IS NOT NULL AND campanha != ''
        {filters}
        GROUP BY campanha
        HAVING SUM(investimento) >= 100
        ORDER BY roas_medio DESC
        LIMIT ?
    """, tuple(params_full))


def top_campanhas_compras(
    limit:       int = 10,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Top N campanhas por volume de compras."""
    filters, params = _build_date_filter(data_inicio, data_fim)
    params_full = params + [limit]
    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(compras), 0)          AS compras,
            ROUND(SUM(valor_compras), 2)    AS receita,
            ROUND(SUM(investimento), 2)     AS investimento,
            ROUND(AVG(roas_compras), 2)     AS roas_medio,
            ROUND(AVG(custo_por_compra), 2) AS custo_por_compra
        FROM campanhas
        WHERE campanha IS NOT NULL AND campanha != ''
        {filters}
        GROUP BY campanha
        ORDER BY compras DESC
        LIMIT ?
    """, tuple(params_full))


def evolucao_diaria(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Evolução diária agregada (todas as campanhas ou uma específica)."""
    conditions = []
    params = []

    if campanha:
        conditions.append("campanha = ?")
        params.append(campanha)
    if data_inicio:
        conditions.append("data_inicio >= ?")
        params.append(data_inicio)
    if data_fim:
        conditions.append("data_fim <= ?")
        params.append(data_fim)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    return query(f"""
        SELECT
            data_inicio                            AS data,
            ROUND(SUM(investimento), 2)            AS investimento,
            ROUND(SUM(valor_compras), 2)           AS receita,
            ROUND(SUM(compras), 0)                 AS compras,
            ROUND(AVG(roas_compras), 2)            AS roas_medio,
            ROUND(SUM(impressoes), 0)              AS impressoes,
            ROUND(SUM(cliques_link), 0)            AS cliques,
            ROUND(AVG(ctr_link), 4)                AS ctr_medio
        FROM campanhas
        {where}
        GROUP BY data_inicio
        ORDER BY data_inicio ASC
    """, tuple(params))


def resumo_periodo(data_inicio: str, data_fim: str) -> dict:
    """Totais consolidados de um período."""
    return query_one("""
        SELECT
            COUNT(DISTINCT campanha)              AS campanhas_ativas,
            ROUND(SUM(investimento), 2)           AS investimento_total,
            ROUND(SUM(valor_compras), 2)          AS receita_total,
            ROUND(SUM(compras), 0)                AS compras_total,
            ROUND(AVG(roas_compras), 2)           AS roas_medio,
            ROUND(SUM(impressoes), 0)             AS impressoes_total,
            ROUND(SUM(alcance), 0)                AS alcance_total,
            ROUND(SUM(cliques_link), 0)           AS cliques_total,
            ROUND(AVG(ctr_link), 4)               AS ctr_medio,
            ROUND(AVG(cpc_link), 2)               AS cpc_medio,
            ROUND(AVG(custo_por_compra), 2)       AS custo_medio_por_compra,
            ROUND(AVG(frequencia), 2)             AS frequencia_media
        FROM campanhas
        WHERE data_inicio >= ? AND data_fim <= ?
    """, (data_inicio, data_fim)) or {}


def alertas_roas_baixo(threshold: float = 1.0) -> list[dict]:
    """Campanhas com ROAS abaixo do threshold nos últimos 30 dias."""
    return query("""
        SELECT
            campanha,
            ROUND(AVG(roas_compras), 2)     AS roas_medio,
            ROUND(SUM(investimento), 2)     AS investimento,
            ROUND(SUM(valor_compras), 2)    AS receita,
            MAX(data_fim)                   AS ultima_data
        FROM campanhas
        WHERE data_inicio >= DATE('now', '-30 days')
          AND campanha IS NOT NULL AND campanha != ''
        GROUP BY campanha
        HAVING roas_medio IS NOT NULL AND roas_medio < ?
        ORDER BY roas_medio ASC
    """, (threshold,))


def comparar_campanhas(
    campanhas:   list[str],
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Compara múltiplas campanhas lado a lado."""
    if not campanhas:
        return []
    placeholders = ", ".join(["?"] * len(campanhas))
    filters, date_params = _build_date_filter(data_inicio, data_fim)
    params = list(campanhas) + date_params

    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(investimento), 2)          AS investimento,
            ROUND(SUM(valor_compras), 2)          AS receita,
            ROUND(SUM(compras), 0)                AS compras,
            ROUND(AVG(roas_compras), 2)           AS roas_medio,
            ROUND(AVG(cpc_link), 2)               AS cpc_medio,
            ROUND(AVG(ctr_link), 4)               AS ctr_medio,
            ROUND(AVG(custo_por_compra), 2)       AS custo_por_compra,
            ROUND(AVG(frequencia), 2)             AS frequencia_media,
            ROUND(SUM(impressoes), 0)             AS impressoes,
            ROUND(SUM(alcance), 0)                AS alcance,
            MIN(data_inicio)                      AS data_inicio,
            MAX(data_fim)                         AS data_fim
        FROM campanhas
        WHERE campanha IN ({placeholders})
        {filters}
        GROUP BY campanha
        ORDER BY investimento DESC
    """, tuple(params))


def metricas_funil(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> dict:
    """Métricas de funil: impressões → cliques → carrinho → checkout → compra."""
    conditions = []
    params = []

    if campanha:
        conditions.append("campanha = ?")
        params.append(campanha)
    if data_inicio:
        conditions.append("data_inicio >= ?")
        params.append(data_inicio)
    if data_fim:
        conditions.append("data_fim <= ?")
        params.append(data_fim)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    return query_one(f"""
        SELECT
            ROUND(SUM(impressoes), 0)             AS impressoes,
            ROUND(SUM(cliques_link), 0)           AS cliques,
            ROUND(SUM(visualizacoes_landing), 0)  AS landing_views,
            ROUND(SUM(views_conteudo), 0)         AS views_conteudo,
            ROUND(SUM(adicoes_carrinho), 0)       AS adicoes_carrinho,
            ROUND(SUM(checkout_iniciado), 0)      AS checkout_iniciado,
            ROUND(SUM(compras), 0)                AS compras,
            ROUND(SUM(investimento), 2)           AS investimento,
            ROUND(SUM(valor_compras), 2)          AS receita
        FROM campanhas
        {where}
    """, tuple(params)) or {}


# ── helpers internos ──────────────────────────────────────────────────────────

def _build_date_filter(
    data_inicio: Optional[str],
    data_fim:    Optional[str],
) -> tuple[str, list]:
    conditions = []
    params = []
    if data_inicio:
        conditions.append("AND data_inicio >= ?")
        params.append(data_inicio)
    if data_fim:
        conditions.append("AND data_fim <= ?")
        params.append(data_fim)
    return " ".join(conditions), params
