"""
agent/queries/criativos.py

Criativos: ROAS, fadiga, engajamento, mensageria, vídeo.
"""
import logging
from typing import Optional

from db.connection import query, query_one, DB_PATH
from utils.formatters import fmt_brl

logger = logging.getLogger(__name__)

from agent.queries.base import _build_where, _meta_ads_union_source, _meta_ads_where

def top_criativos_roas(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    limit:       int = 10,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Top anúncios (criativos) por ROAS dentro de uma campanha ou de todas.
    Agrupa por ad_name + ad_id para análise de criativo individual.
    Usa tabelas brutas Meta Ads (a view campanhas_unificada não expõe ad_name/ad_id).
    """
    from_clause = _meta_ads_union_source(cliente_key)
    where, params = _meta_ads_where(
        "ad_name IS NOT NULL AND ad_name != '' AND investimento > 0",
        campanha=campanha,
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
    )
    _roas_raw = "ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4)"
    return query(f"""
        SELECT
            campanha,
            ad_name                                               AS criativo,
            ad_id,
            ROUND(SUM(investimento), 2)                           AS investimento,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2)             AS receita,
            {_roas_raw}                                           AS roas,
            ROUND(SUM(COALESCE(compras, 0)), 0)                   AS compras,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(compras,0)),0), 2) AS cpa,
            ROUND(SUM(impressoes), 0)                             AS impressoes,
            ROUND(CAST(SUM(COALESCE(cliques_link,0)) AS FLOAT) / NULLIF(SUM(impressoes),0) * 100, 2) AS ctr,
            ROUND(CAST(SUM(COALESCE(impressoes,0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance,0)),0), 2) AS frequencia,
            MAX(data)                                             AS ultima_atividade
        {from_clause}
        {where}
        GROUP BY campanha, ad_name, ad_id
        HAVING SUM(investimento) > 10
        ORDER BY roas DESC NULLS LAST
        LIMIT {limit}
    """, tuple(params))



def criativos_em_fadiga(
    frequencia_min: float = 3.5,
    data_inicio:    Optional[str] = None,
    data_fim:       Optional[str] = None,
    cliente_key:    Optional[str] = None,
    plataforma:     Optional[str] = None,
) -> list[dict]:
    """
    Criativos com frequência alta (fadiga) e CTR abaixo da média — candidatos a pausar ou renovar.
    Usa tabelas brutas Meta Ads (a view campanhas_unificada não expõe ad_name/ad_id).
    """
    from_clause = _meta_ads_union_source(cliente_key)
    where, params = _meta_ads_where(
        "ad_name IS NOT NULL AND ad_name != '' AND investimento > 0",
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
    )
    _roas_raw = "ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4)"
    return query(f"""
        WITH base AS (
            SELECT
                campanha,
                ad_name AS criativo,
                ad_id,
                ROUND(SUM(investimento), 2) AS investimento,
                ROUND(CAST(SUM(COALESCE(impressoes,0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance,0)),0), 2) AS frequencia,
                ROUND(CAST(SUM(COALESCE(cliques_link,0)) AS FLOAT) / NULLIF(SUM(impressoes),0) * 100, 2) AS ctr,
                ROUND(SUM(COALESCE(compras,0)), 0) AS compras,
                {_roas_raw} AS roas,
                MAX(data) AS ultima_atividade
            {from_clause}
            {where}
            GROUP BY campanha, ad_name, ad_id
            HAVING SUM(investimento) > 20
               AND SUM(COALESCE(alcance,0)) > 0
        ),
        media_ctr AS (
            SELECT AVG(ctr) as ctr_medio FROM base WHERE ctr > 0
        )
        SELECT b.*,
               ROUND(m.ctr_medio, 2) AS ctr_medio_conta
        FROM base b, media_ctr m
        WHERE b.frequencia >= {frequencia_min}
          AND (b.ctr < m.ctr_medio OR b.ctr IS NULL)
        ORDER BY b.frequencia DESC, b.investimento DESC
    """, tuple(params))



def metricas_engajamento(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Métricas de engajamento social por campanha.

    Curtidas, comentários, compartilhamentos, salvamentos, reações.
    Útil para campanhas de awareness/engajamento onde o KPI não é conversão.
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
            ROUND(SUM(COALESCE(engajamentos_post, 0)), 0)   AS engajamentos_post,
            ROUND(SUM(COALESCE(comentarios, 0)), 0)         AS comentarios,
            ROUND(SUM(COALESCE(compartilhamentos, 0)), 0)   AS compartilhamentos,
            ROUND(SUM(COALESCE(salvamentos, 0)), 0)         AS salvamentos,
            ROUND(SUM(COALESCE(reacoes, 0)), 0)             AS reacoes,
            ROUND(SUM(COALESCE(curtidas_facebook, 0)), 0)   AS curtidas_facebook,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(engajamentos_post, 0)), 0), 2) AS custo_por_engajamento,
            ROUND(SUM(investimento), 2)                     AS investimento,
            ROUND(SUM(impressoes), 0)                       AS impressoes,
            ROUND(
                CAST(SUM(COALESCE(engajamentos_post, 0)) AS FLOAT)
                / NULLIF(SUM(impressoes), 0) * 100
            , 2)                                            AS taxa_engajamento_pct
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(COALESCE(engajamentos_post, 0)) > 0
           OR SUM(COALESCE(compartilhamentos, 0)) > 0
        ORDER BY engajamentos_post DESC
    """, tuple(params))



def metricas_mensageria(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Métricas de campanhas de mensageria (WhatsApp/Messenger).

    Conversas iniciadas, respondidas, custo por conversa e boas-vindas.
    Ideal para negócios que usam Meta para gerar leads por mensagem.
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
            ROUND(SUM(COALESCE(conversas_iniciadas, 0)), 0)     AS conversas_iniciadas,
            ROUND(SUM(COALESCE(conversas_respondidas_7d, 0)), 0) AS conversas_respondidas,
            ROUND(SUM(COALESCE(msg_boas_vindas, 0)), 0)         AS msg_boas_vindas,
            ROUND(SUM(COALESCE(msg_inscricoes, 0)), 0)          AS inscricoes,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(conversas_iniciadas, 0)), 0), 2) AS custo_por_conversa,
            ROUND(SUM(investimento), 2)                         AS investimento,
            ROUND(SUM(impressoes), 0)                           AS impressoes
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(COALESCE(conversas_iniciadas, 0)) > 0
        ORDER BY conversas_iniciadas DESC
    """, tuple(params))



def metricas_video(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Métricas de vídeo por campanha: views, ThruPlays, retenção e custo.

    Retorna para cada campanha:
      - reproducoes_video   : total de reproduções iniciadas
      - video_3s            : visualizações de 3 segundos (métrica padrão Meta)
      - thruplays           : visualizações completas (≥ 15s ou 97% do vídeo)
      - taxa_retencao_pct   : ThruPlays / reproducoes_video * 100
      - custo_thruplay      : custo médio por ThruPlay
      - visualizacoes       : visualizações totais (inclui partilhas)
      - custo_por_view      : investimento / visualizacoes
    """
    base = "campanha = ?" if campanha else None
    extra = [campanha] if campanha else []
    where, params = _build_where(
        base,
        extra_params=extra,
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    rows = query(f"""
        SELECT
            campanha,
            ROUND(SUM(COALESCE(reproducoes_video, 0)), 0)   AS reproducoes_video,
            ROUND(SUM(COALESCE(video_3s, 0)), 0)            AS video_3s,
            ROUND(SUM(COALESCE(thruplays, 0)), 0)           AS thruplays,
            ROUND(
                CAST(SUM(COALESCE(thruplays, 0)) AS FLOAT)
                / NULLIF(SUM(COALESCE(reproducoes_video, 0)), 0) * 100
            , 2)                                            AS taxa_retencao_pct,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(thruplays, 0)), 0), 4) AS custo_thruplay,
            ROUND(SUM(COALESCE(visualizacoes, 0)), 0)       AS visualizacoes,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(visualizacoes, 0)), 0), 4) AS custo_por_view,
            ROUND(SUM(investimento), 2)                     AS investimento
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(COALESCE(reproducoes_video, 0)) > 0
        ORDER BY thruplays DESC
    """, tuple(params))
    return rows



