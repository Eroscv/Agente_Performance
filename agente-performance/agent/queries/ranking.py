"""
agent/queries/ranking.py

Rankings de campanhas por ROAS, compras, CTR, CPM, alcance, cliques, receita.
"""
import logging
from typing import Optional

from db.connection import query, query_one, DB_PATH
from utils.formatters import fmt_brl

logger = logging.getLogger(__name__)

from agent.queries.base import _build_where, _meta_ads_union_source, _meta_ads_where, _ROAS_EXPR, _FREQ_EXPR, AWARENESS_PATTERNS

def top_campanhas_roas(
    limit:                   int = 10,
    data_inicio:             Optional[str] = None,
    data_fim:                Optional[str] = None,
    excluir_awareness:       bool = True,
    exigir_atividade_recente: bool = True,
    cliente_key:             Optional[str] = None,
    plataforma:              Optional[str] = None,
) -> list[dict]:
    """
    Top N campanhas por ROAS ponderado (mínimo R$100 investidos).

    Retorna metadado 'investimento_minimo_filtro' para transparência.
    Campanhas de awareness/tráfego são excluídas por padrão (excluir_awareness=True)
    pois não têm objetivo de conversão e distorcem o ranking.

    exigir_atividade_recente=True (padrão): exclui campanhas cuja última atividade
    seja anterior ao data_inicio do filtro — evita que campanhas antigas apareçam
    em rankings por ter poucos dias com ROAS inflado.
    """
    INVEST_MIN = 100
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )

    # Filtro de awareness: exclui campanhas cujo nome contém padrões de awareness/tráfego
    awareness_filter = ""
    if excluir_awareness:
        awareness_clauses = " AND ".join(
            f"LOWER(campanha) NOT LIKE '%{p}%'" for p in AWARENESS_PATTERNS
        )
        awareness_filter = f" AND ({awareness_clauses})"

    # Filtro de atividade recente: só campanhas com dados dentro do período pedido
    # (HAVING MAX(data_fim) >= data_inicio garante que a campanha estava ativa no período)
    atividade_filter = ""
    if exigir_atividade_recente and data_inicio:
        atividade_filter = f" AND MAX(data_fim) >= '{data_inicio}'"

    rows = query(f"""
        SELECT
            campanha,
            {_ROAS_EXPR}                                AS roas,
            ROUND(SUM(investimento), 2)                 AS investimento,
            ROUND(SUM(valor_compras), 2)                AS receita,
            ROUND(SUM(compras), 0)                      AS compras,
            MAX(data_fim)                               AS ultima_atividade
        FROM campanhas_unificada
        {where}{awareness_filter}
        GROUP BY campanha
        HAVING SUM(investimento) >= {INVEST_MIN}
           AND SUM(valor_compras) > 0
           {atividade_filter}
        ORDER BY roas DESC
        LIMIT ?
    """, tuple(params + [limit]))

    if not rows:
        logger.warning("[queries] top_campanhas_roas: nenhum resultado (período: %s → %s, invest_min: R$%s, excluir_awareness=%s)",
                       data_inicio, data_fim, INVEST_MIN, excluir_awareness)
    return rows



def top_campanhas_compras(
    limit:       int = 10,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """Top N campanhas por volume de compras."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(COALESCE(compras, 0)), 0)                AS compras,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2)             AS receita,
            ROUND(SUM(investimento), 2)                          AS investimento,
            {_ROAS_EXPR}                                         AS roas,
            ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS cpa_ajustado
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        ORDER BY compras DESC
        LIMIT ?
    """, tuple(params + [limit]))



def top_campanhas_ctr(
    limit:       int = 10,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """Top N campanhas por CTR (mínimo 1.000 impressões para evitar distorção)."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            campanha,
            ROUND(CAST(SUM(COALESCE(cliques_link, 0)) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4) AS ctr,
            ROUND(SUM(COALESCE(cliques_link, 0)), 0)  AS cliques,
            ROUND(SUM(impressoes), 0)                  AS impressoes,
            ROUND(SUM(investimento), 2)                AS investimento,
            {_ROAS_EXPR}                               AS roas
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(impressoes) >= 1000
        ORDER BY ctr DESC
        LIMIT ?
    """, tuple(params + [limit]))



def top_campanhas_cpm(
    limit:       int = 10,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """Top N campanhas por menor CPM (mais eficiente em custo de exibição)."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2) AS cpm,
            ROUND(SUM(impressoes), 0)          AS impressoes,
            ROUND(SUM(investimento), 2)        AS investimento,
            {_ROAS_EXPR}                       AS roas,
            ROUND(SUM(COALESCE(compras, 0)), 0) AS compras
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(impressoes) >= 1000
        ORDER BY cpm ASC
        LIMIT ?
    """, tuple(params + [limit]))



def top_campanhas_alcance(
    limit:       int = 10,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """Top N campanhas por alcance total único."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(COALESCE(alcance, 0)), 0)       AS alcance,
            ROUND(SUM(impressoes), 0)                  AS impressoes,
            {_FREQ_EXPR},
            ROUND(SUM(investimento), 2)                AS investimento,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(alcance, 0)), 0) * 1000, 2) AS custo_por_mil_alcance
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(COALESCE(alcance, 0)) > 0
        ORDER BY alcance DESC
        LIMIT ?
    """, tuple(params + [limit]))



def top_campanhas_cliques(
    limit:       int = 10,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """Top N campanhas por volume de cliques no link."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(COALESCE(cliques_link, 0)), 0)  AS cliques,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc,
            ROUND(CAST(SUM(COALESCE(cliques_link, 0)) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4) AS ctr,
            ROUND(SUM(investimento), 2)                AS investimento,
            {_ROAS_EXPR}                               AS roas
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(COALESCE(cliques_link, 0)) > 0
        ORDER BY cliques DESC
        LIMIT ?
    """, tuple(params + [limit]))



def top_campanhas_receita(
    limit:       int = 10,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """Top N campanhas por receita total gerada."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2)  AS receita,
            ROUND(SUM(COALESCE(compras, 0)), 0)        AS compras,
            ROUND(SUM(investimento), 2)                AS investimento,
            {_ROAS_EXPR}                               AS roas,
            ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS cpa_ajustado
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(COALESCE(valor_compras, 0)) > 0
        ORDER BY receita DESC
        LIMIT ?
    """, tuple(params + [limit]))



def eficiencia_criativo(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Indicadores de qualidade de criativo por campanha.

    Retorna CPM (custo de exibição), CTR (qualidade do clique),
    CPC e hook rate estimado (video_3s / impressoes).
    Métricas proxy para avaliar se o criativo é bom.
    """
    base  = "campanha = ?" if campanha else None
    extra = [campanha] if campanha else []
    where, params = _build_where(
        base,
        extra_params=extra,
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2)   AS cpm,
            ROUND(CAST(SUM(COALESCE(cliques_link, 0)) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4) AS ctr,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc,
            ROUND(CAST(SUM(COALESCE(video_3s, 0)) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 2) AS hook_rate_pct,
            ROUND(CAST(SUM(COALESCE(thruplays, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(reproducoes_video, 0)), 0) * 100, 2) AS retencao_video_pct,
            ROUND(SUM(impressoes), 0)              AS impressoes,
            ROUND(SUM(COALESCE(alcance, 0)), 0)    AS alcance,
            ROUND(SUM(investimento), 2)            AS investimento,
            {_ROAS_EXPR}                           AS roas
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(impressoes) >= 500
        ORDER BY ctr DESC
    """, tuple(params))



