"""
agent/queries/alertas.py

Alertas: ROAS baixo, campanhas sem conversão, inativas, fadiga.
"""
import logging
from typing import Optional

from db.connections import query_fed as query, query_fed_one as query_one, LEGACY_DB_PATH as DB_PATH
from utils.formatters import fmt_brl

logger = logging.getLogger(__name__)

from agent.queries.base import _build_where, _meta_ads_union_source, _meta_ads_where, _ROAS_EXPR, data_range_disponivel

def alertas_roas_baixo(
    threshold:   float = 1.0,
    janela_dias: int = 30,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Campanhas com ROAS preocupante.

    Usa a data máxima do banco (não DATE('now')) para funcionar mesmo
    com dados defasados. Janela configurável via janela_dias (padrão: 30).
    """
    dr = data_range_disponivel()
    data_max = dr.get("data_maxima")
    if not data_max:
        logger.warning("[queries] alertas_roas_baixo: banco vazio")
        return []

    logger.debug("[queries] alertas_roas_baixo: data_max=%s threshold=%.2f janela=%dd",
                 data_max, threshold, janela_dias)
    janela_sql = f"-{janela_dias} days"

    # Filtros extras de cliente/plataforma
    extra_conditions = ""
    extra_params_list: list = []
    if cliente_key:
        extra_conditions += "\n          AND cliente_key = ?"
        extra_params_list.append(cliente_key)
    if plataforma:
        extra_conditions += "\n          AND plataforma = ?"
        extra_params_list.append(plataforma)

    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4) AS roas,
            ROUND(SUM(investimento), 2)     AS investimento,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2)    AS receita,
            ROUND(SUM(COALESCE(compras, 0)), 0)          AS compras,
            MAX(data_fim)                   AS ultima_data,
            CASE
                WHEN SUM(valor_compras) = 0 OR SUM(investimento) = 0 THEN 'sem_conversao'
                WHEN SUM(valor_compras) / NULLIF(SUM(investimento), 0) < ? THEN 'roas_baixo'
                ELSE 'ok'
            END AS tipo_alerta
        FROM campanhas_unificada
        WHERE data_inicio >= DATE(?, ?)
          AND campanha IS NOT NULL AND campanha != ''
          AND LOWER(campanha) NOT LIKE '%engajamento%'
          AND LOWER(campanha) NOT LIKE '%trafego%'
          AND LOWER(campanha) NOT LIKE '%tr_fego%'{extra_conditions}
        GROUP BY campanha
        HAVING SUM(investimento) >= 100
           AND (
               SUM(valor_compras) = 0
               OR SUM(valor_compras) / NULLIF(SUM(investimento), 0) < ?
           )
        ORDER BY
            CASE WHEN SUM(valor_compras) = 0 THEN 0 ELSE 1 END ASC,
            SUM(valor_compras) / NULLIF(SUM(investimento), 0) ASC
    """, (threshold, data_max, janela_sql, *extra_params_list, threshold))



def campanhas_sem_conversao(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    invest_min:  float = 50.0,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Campanhas com investimento mas ZERO compras registradas.

    São candidatas a revisão imediata — dinheiro sendo gasto sem retorno.
    invest_min: mínimo de investimento para aparecer na lista (default R$50).
    """
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
            ROUND(SUM(investimento), 2)           AS investimento,
            ROUND(SUM(impressoes), 0)             AS impressoes,
            ROUND(SUM(COALESCE(cliques_link, 0)), 0) AS cliques,
            ROUND(SUM(COALESCE(adicoes_carrinho, 0)), 0) AS carrinho,
            ROUND(SUM(COALESCE(checkout_iniciado, 0)), 0) AS checkout,
            MAX(data_fim)                         AS ultima_atividade
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(investimento) >= {invest_min}
           AND (SUM(COALESCE(compras, 0)) = 0 OR SUM(COALESCE(valor_compras, 0)) = 0)
        ORDER BY investimento DESC
    """, tuple(params))



def campanhas_inativas(
    dias_sem_dados: int = 14,
    cliente_key:    Optional[str] = None,
    plataforma:     Optional[str] = None,
) -> list[dict]:
    """
    Campanhas que não têm dados nos últimos N dias.

    Identifica campanhas pausadas, encerradas ou com problema de tracking.
    dias_sem_dados: considera inativa se a última atividade foi há mais de N dias.
    """
    dr = data_range_disponivel()
    data_max = dr.get("data_maxima")
    if not data_max:
        return []

    # Filtros extras de cliente/plataforma
    extra_conditions = ""
    extra_params_list: list = []
    if cliente_key:
        extra_conditions += " AND cliente_key = ?"
        extra_params_list.append(cliente_key)
    if plataforma:
        extra_conditions += " AND plataforma = ?"
        extra_params_list.append(plataforma)

    return query(f"""
        SELECT
            campanha,
            MAX(data_fim)                        AS ultima_atividade,
            ROUND(SUM(investimento), 2)          AS investimento_total,
            ROUND(SUM(COALESCE(compras, 0)), 0)  AS compras_total,
            {_ROAS_EXPR}                         AS roas_historico,
            COUNT(DISTINCT data_inicio)          AS dias_com_dados
        FROM campanhas_unificada
        WHERE campanha IS NOT NULL AND campanha != ''{extra_conditions}
        GROUP BY campanha
        HAVING MAX(data_fim) < DATE(?, ?)
           AND SUM(investimento) >= 100
        ORDER BY ultima_atividade DESC
    """, (*extra_params_list, data_max, f"-{dias_sem_dados} days"))



def saturacao_frequencia(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    threshold:   float = 3.0,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Campanhas com frequência média acima do threshold (padrão: 3x).

    Alta frequência indica saturação de audiência — o público está vendo
    o mesmo anúncio muitas vezes, o que costuma degradar o ROAS.
    """
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
            ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia_media,
            ROUND(SUM(impressoes), 0)              AS impressoes,
            ROUND(SUM(COALESCE(alcance, 0)), 0)    AS alcance,
            {_ROAS_EXPR}                           AS roas,
            ROUND(SUM(investimento), 2)            AS investimento,
            ROUND(SUM(COALESCE(compras, 0)), 0)    AS compras,
            ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2) AS cpm
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING frequencia_media >= {threshold}
           AND SUM(COALESCE(alcance, 0)) > 0
        ORDER BY frequencia_media DESC
    """, tuple(params))



