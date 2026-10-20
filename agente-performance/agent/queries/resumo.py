"""
agent/queries/resumo.py

Resumos agregados por campanha, período e executivo.
"""
import logging
from typing import Optional

from db.connections import query_fed as query, query_fed_one as query_one, LEGACY_DB_PATH as DB_PATH
from utils.formatters import fmt_brl

logger = logging.getLogger(__name__)

from agent.queries.base import _build_where, _meta_ads_union_source, _meta_ads_where, _ROAS_EXPR, _FREQ_EXPR, AWARENESS_PATTERNS, data_range_disponivel
from agent.tz import hoje_brt

def resumo_por_campanha(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """Resumo agregado por campanha: investimento, compras, ROAS ponderado, CPC, CTR."""
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
            ROUND(SUM(investimento), 2)                                       AS investimento,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2)                         AS receita,
            ROUND(SUM(COALESCE(compras, 0)), 0)                               AS compras,
            {_ROAS_EXPR}                                                      AS roas,
            ROUND(SUM(impressoes), 0)                                         AS impressoes,
            ROUND(SUM(alcance), 0)                                            AS alcance,
            ROUND(SUM(COALESCE(cliques_link, 0)), 0)                          AS cliques,
            ROUND(CAST(SUM(COALESCE(cliques_link, 0)) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4) AS ctr_medio,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc_medio,
            ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS cpa_ajustado,
            {_FREQ_EXPR},
            COUNT(*)                                                          AS dias
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        ORDER BY investimento DESC
    """, tuple(params))



def performance_campanha(
    campanha:    str,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Série temporal diária de uma campanha específica.

    Tenta match exato primeiro; se não encontrar, usa LIKE com o termo fornecido.
    Loga qual caminho foi utilizado para facilitar diagnóstico.
    """
    where_exato, params_exato = _build_where(
        "campanha = ?",
        extra_params=[campanha],
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    resultado = query(f"""
        SELECT
            data_inicio                           AS data,
            campanha,
            ROUND(investimento, 2)                AS investimento,
            ROUND(valor_compras, 2)               AS receita,
            compras,
            ROUND(roas_compras, 4)                AS roas,
            impressoes,
            alcance,
            cliques_link,
            ROUND(ctr_link, 4)                    AS ctr,
            ROUND(cpc_link, 2)                    AS cpc,
            ROUND(custo_por_compra, 2)            AS custo_por_compra,
            ROUND(frequencia, 2)                  AS frequencia,
            adicoes_carrinho,
            checkout_iniciado
        FROM campanhas_unificada
        {where_exato}
        ORDER BY data_inicio ASC
    """, tuple(params_exato))

    if resultado:
        logger.debug("[queries] performance_campanha: match exato '%s' → %d dias", campanha, len(resultado))
        return resultado

    # Fallback: LIKE case-insensitive (LOWER funciona em SQLite e DuckDB)
    like_pattern = f"%{campanha.lower()}%"
    where_like, params_like = _build_where(
        "LOWER(campanha) LIKE ?",
        extra_params=[like_pattern],
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    logger.info("[queries] performance_campanha: match exato vazio para '%s', tentando LIKE '%s'", campanha, like_pattern)
    return query(f"""
        SELECT
            data_inicio                           AS data,
            campanha,
            ROUND(SUM(investimento), 2)           AS investimento,
            ROUND(SUM(valor_compras), 2)          AS receita,
            ROUND(SUM(compras), 0)                AS compras,
            ROUND(SUM(valor_compras) / NULLIF(SUM(investimento), 0), 4) AS roas,
            ROUND(SUM(impressoes), 0)             AS impressoes,
            ROUND(SUM(alcance), 0)                AS alcance,
            ROUND(SUM(cliques_link), 0)           AS cliques_link,
            ROUND(CAST(SUM(cliques_link) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4) AS ctr,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc,
            ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS cpa_ajustado,
            ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia_media,
            ROUND(SUM(COALESCE(adicoes_carrinho, 0)), 0) AS adicoes_carrinho,
            ROUND(SUM(COALESCE(checkout_iniciado, 0)), 0) AS checkout_iniciado
        FROM campanhas_unificada
        {where_like}
        GROUP BY data_inicio, campanha
        ORDER BY data_inicio ASC
    """, tuple(params_like))


# AWARENESS_PATTERNS importado de agent.queries.base



def resumo_periodo(
    data_inicio: str,
    data_fim:    str,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> dict:
    """
    Totais consolidados de um período.

    Retorna dict vazio se não há dados — inclui 'aviso' quando isso ocorre.
    """
    where, params = _build_where(
        None,
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    result = query_one(f"""
        SELECT
            COUNT(DISTINCT campanha)                         AS campanhas_ativas,
            ROUND(SUM(investimento), 2)                      AS investimento_total,
            ROUND(SUM(valor_compras), 2)                     AS receita_total,
            ROUND(SUM(compras), 0)                           AS compras_total,
            {_ROAS_EXPR}                                     AS roas,
            ROUND(SUM(impressoes), 0)                        AS impressoes_total,
            ROUND(SUM(alcance), 0)                           AS alcance_total,
            ROUND(SUM(cliques_link), 0)                      AS cliques_total,
            ROUND(SUM(COALESCE(adicoes_carrinho, 0)), 0)     AS carrinho_total,
            ROUND(SUM(COALESCE(checkout_iniciado, 0)), 0)    AS checkout_total,
            ROUND(CAST(SUM(COALESCE(cliques_link, 0)) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4) AS ctr_medio,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc_medio,
            ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2) AS cpm_medio,
            ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS custo_medio_por_compra,
            ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia_media,
            MIN(data_inicio)                                 AS data_inicio_real,
            MAX(data_fim)                                    AS data_fim_real
        FROM campanhas_unificada
        {where}
    """, tuple(params))

    if not result or not result.get("investimento_total"):
        dr = data_range_disponivel()
        logger.warning(
            "[queries] resumo_periodo: sem dados para %s → %s. Banco cobre %s → %s.",
            data_inicio, data_fim,
            dr.get("data_minima"), dr.get("data_maxima"),
        )
        return {
            "aviso": f"Nenhum dado encontrado para o período {data_inicio} → {data_fim}.",
            "banco_cobre": f"{dr.get('data_minima')} → {dr.get('data_maxima')}",
        }

    return result



def resumo_executivo(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> dict:
    """
    Visão executiva completa: totais + top campanhas + alertas.

    Agrega em uma única chamada:
    - Totais do período
    - Top 3 por ROAS, compras e receita
    - Campanhas com ROAS baixo
    - Campanhas sem conversão
    - Distribuição de investimento

    Retorna dict com todas essas seções.
    """
    _fk: dict = {}
    if cliente_key:
        _fk["cliente_key"] = cliente_key
    if plataforma:
        _fk["plataforma"] = plataforma

    totais    = resumo_periodo(data_inicio, data_fim, **_fk) if (data_inicio and data_fim) else {}
    top_roas  = top_campanhas_roas(3, data_inicio, data_fim, **_fk)
    top_comp  = top_campanhas_compras(3, data_inicio, data_fim, **_fk)
    top_rec   = top_campanhas_receita(3, data_inicio, data_fim, **_fk)
    alertas   = alertas_roas_baixo(**_fk)
    sem_conv  = campanhas_sem_conversao(data_inicio, data_fim, **_fk)
    distrib   = distribuicao_investimento(data_inicio, data_fim, **_fk)

    return {
        "totais":             totais,
        "top_roas":           top_roas,
        "top_compras":        top_comp,
        "top_receita":        top_rec,
        "alertas_roas":       alertas,
        "sem_conversao":      sem_conv,
        "distribuicao":       distrib,
        "periodo":            {"inicio": data_inicio, "fim": data_fim},
    }


# ── Shopify — receita real sincronizada ───────────────────────────────────────


def historico_mensal(
    campanha:    Optional[str] = None,
    meses:       int = 12,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Série histórica mensal: últimos N meses com totais e ROAS.
    Útil para visualizar sazonalidade e tendências de longo prazo.
    """
    import calendar as _cal
    from datetime import date as _date

    hoje = _hoje_brt()
    resultado = []

    for i in range(meses - 1, -1, -1):
        ano = hoje.year
        mes = hoje.month - i
        while mes <= 0:
            mes += 12
            ano -= 1

        ini = _date(ano, mes, 1).isoformat()
        fim = _date(ano, mes, _cal.monthrange(ano, mes)[1]).isoformat()

        base = "campanha = ?" if campanha else None
        extra = [campanha] if campanha else []
        where, params = _build_where(
            base, extra_params=extra, data_inicio=ini, data_fim=fim,
            cliente_key=cliente_key, plataforma=plataforma,
        )

        nomes = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
        row = query(f"""
            SELECT
                '{nomes[mes-1]}/{str(ano)[2:]}' AS mes_label,
                '{ini}'                          AS data_inicio,
                '{fim}'                          AS data_fim,
                ROUND(SUM(investimento), 2)      AS investimento,
                ROUND(SUM(COALESCE(valor_compras, 0)), 2)     AS receita,
                ROUND(SUM(COALESCE(compras, 0)), 0)           AS compras,
                {_ROAS_EXPR}                                  AS roas,
                ROUND(SUM(impressoes), 0)        AS impressoes,
                ROUND(SUM(COALESCE(cliques_link, 0)), 0)      AS cliques,
                ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2) AS cpm,
                COUNT(DISTINCT campanha)         AS campanhas_ativas
            FROM campanhas_unificada
            {where}
        """, tuple(params))

        if row and row[0].get("investimento"):
            resultado.append(row[0])

    return resultado



def metricas_funil(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> dict:
    """Métricas de funil: impressões → cliques → carrinho → checkout → compra."""
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
    return query_one(f"""
        SELECT
            ROUND(SUM(impressoes), 0)                              AS impressoes,
            ROUND(SUM(COALESCE(cliques_link, 0)), 0)               AS cliques,
            ROUND(SUM(COALESCE(visualizacoes_landing, 0)), 0)      AS landing_views,
            ROUND(SUM(COALESCE(visualizacoes_landing, 0)), 0)      AS views_conteudo,
            ROUND(SUM(COALESCE(adicoes_carrinho, 0)), 0)           AS adicoes_carrinho,
            ROUND(SUM(COALESCE(checkout_iniciado, 0)), 0)          AS checkout_iniciado,
            ROUND(SUM(COALESCE(compras, 0)), 0)                    AS compras,
            ROUND(SUM(investimento), 2)                            AS investimento,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2)              AS receita
        FROM campanhas_unificada
        {where}
    """, tuple(params)) or {}


# ── helper interno ────────────────────────────────────────────────────────────


def custo_funil_detalhado(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Custo em cada etapa do funil por campanha.

    Retorna custo por: clique → landing → view conteúdo → carrinho → checkout → compra.
    Ideal para identificar onde o funil está 'vazando' dinheiro.
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
            ROUND(SUM(investimento), 2)           AS investimento,
            -- Clique
            ROUND(SUM(COALESCE(cliques_link, 0)), 0)      AS cliques,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc,
            -- Landing page
            ROUND(SUM(COALESCE(visualizacoes_landing, 0)), 0)  AS landing_views,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(visualizacoes_landing, 0)), 0), 2) AS custo_por_landing,
            -- Visualização de conteúdo (mapeado de visualizacoes_landing no schema)
            ROUND(SUM(COALESCE(visualizacoes_landing, 0)), 0)      AS views_conteudo,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(visualizacoes_landing, 0)), 0), 2) AS custo_por_view_conteudo,
            -- Carrinho
            ROUND(SUM(COALESCE(adicoes_carrinho, 0)), 0)   AS carrinho,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(adicoes_carrinho, 0)), 0), 2) AS custo_por_carrinho,
            -- Checkout
            ROUND(SUM(COALESCE(checkout_iniciado, 0)), 0)  AS checkout,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(checkout_iniciado, 0)), 0), 2) AS custo_por_checkout,
            -- Compra
            ROUND(SUM(COALESCE(compras, 0)), 0)            AS compras,
            ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS cpa_ajustado,
            -- Taxas de conversão no funil
            ROUND(CAST(SUM(COALESCE(adicoes_carrinho, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0) * 100, 2) AS taxa_click_carrinho_pct,
            ROUND(CAST(SUM(COALESCE(checkout_iniciado, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(adicoes_carrinho, 0)), 0) * 100, 2) AS taxa_carrinho_checkout_pct,
            ROUND(CAST(SUM(COALESCE(compras, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(checkout_iniciado, 0)), 0) * 100, 2) AS taxa_checkout_compra_pct,
            ROUND(CAST(SUM(COALESCE(compras, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0) * 100, 2) AS taxa_conversao_geral_pct
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        HAVING SUM(investimento) >= 50
        ORDER BY investimento DESC
    """, tuple(params))



def distribuicao_investimento(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Share de investimento por campanha (% do total do período).

    Mostra onde está indo o budget e o ROAS de cada fatia.
    Útil para identificar se o dinheiro está indo para as campanhas certas.
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
            ROUND(SUM(investimento), 2)          AS investimento,
            ROUND(
                SUM(investimento) * 100.0 /
                NULLIF(SUM(SUM(investimento)) OVER (), 0)
            , 1)                                 AS share_pct,
            {_ROAS_EXPR}                         AS roas,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2) AS receita,
            ROUND(SUM(COALESCE(compras, 0)), 0)  AS compras
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        ORDER BY investimento DESC
    """, tuple(params))



def projecao_fechamento_mes(
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> dict:
    """
    Projeta o resultado do mês corrente com base no ritmo dos dias já disponíveis.
    Retorna: dias_decorridos, dias_mes, ritmo_diario, projecao_invest, projecao_receita,
             projecao_roas, projecao_compras, dados_reais_ate_agora.
    """
    from datetime import date, timedelta
    import calendar

    hoje = hoje_brt()
    primeiro_dia = hoje.replace(day=1)
    ultimo_dia_mes = hoje.replace(day=calendar.monthrange(hoje.year, hoje.month)[1])
    dias_mes = ultimo_dia_mes.day
    dias_decorridos = (hoje - primeiro_dia).days  # dias com dados (exclusive hoje)

    if dias_decorridos <= 0:
        return {"aviso": "Primeiro dia do mês — sem dados suficientes para projeção."}

    where, params = _build_where(
        None,
        data_inicio=str(primeiro_dia),
        data_fim=str(hoje - timedelta(days=1)),
        cliente_key=cliente_key,
        plataforma=plataforma,
    )

    r = query_one(f"""
        SELECT
            ROUND(SUM(investimento), 2)               AS invest_real,
            ROUND(SUM(COALESCE(valor_compras,0)), 2)  AS receita_real,
            ROUND(SUM(COALESCE(compras,0)), 0)        AS compras_real,
            COUNT(DISTINCT date(data_inicio))         AS dias_com_dados
        FROM campanhas_unificada {where}
    """, tuple(params))

    if not r or not r.get("invest_real"):
        return {"aviso": "Sem dados suficientes para projeção do mês corrente."}

    invest_real  = r["invest_real"] or 0
    receita_real = r["receita_real"] or 0
    compras_real = r["compras_real"] or 0
    dias_dados   = r["dias_com_dados"] or dias_decorridos

    # Ritmo diário médio
    ritmo_invest  = invest_real  / dias_dados
    ritmo_receita = receita_real / dias_dados
    ritmo_compras = compras_real / dias_dados

    # Projeção para o mês completo
    proj_invest  = round(ritmo_invest  * dias_mes, 2)
    proj_receita = round(ritmo_receita * dias_mes, 2)
    proj_compras = round(ritmo_compras * dias_mes, 0)
    proj_roas    = round(proj_receita / proj_invest, 4) if proj_invest > 0 else None

    dias_restantes = dias_mes - dias_decorridos

    return {
        "mes":              hoje.strftime("%B/%Y"),
        "dias_mes":         dias_mes,
        "dias_decorridos":  dias_decorridos,
        "dias_restantes":   dias_restantes,
        "invest_real":      invest_real,
        "receita_real":     receita_real,
        "compras_real":     compras_real,
        "roas_real":        round(receita_real / invest_real, 4) if invest_real > 0 else None,
        "ritmo_invest_dia": round(ritmo_invest, 2),
        "proj_invest_mes":  proj_invest,
        "proj_receita_mes": proj_receita,
        "proj_compras_mes": int(proj_compras),
        "proj_roas":        proj_roas,
        "pct_mes_decorrido": round(dias_decorridos / dias_mes * 100, 1),
    }



