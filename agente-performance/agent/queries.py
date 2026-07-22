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
  - data_range_disponivel()

Robustez (v2):
  - ROAS sempre como média ponderada pelo investimento (evita distorção de AVG simples)
  - alertas_roas_baixo usa data máxima do banco (não DATE('now')) — funciona com dados defasados
  - _build_date_filter gera cláusulas sempre com AND, seguro para qualquer contexto WHERE
  - performance_campanha loga qual caminho (exato/LIKE) foi usado
  - resumo_periodo e top_campanhas_roas retornam metadados de cobertura
  - data_range_disponivel() expõe min/max do banco para diagnóstico
"""

import logging
from typing import Optional
from db.connection import query, query_one

logger = logging.getLogger(__name__)

# ROAS como média ponderada: SUM(valor_compras) / NULLIF(SUM(investimento), 0)
# Isso é matematicamente equivalente ao ROAS real do período — não distorce como AVG simples.
_ROAS_EXPR = "ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4)"

# Expressão de frequência correta: ponderada por alcance, não AVG simples.
_FREQ_EXPR = "ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia_media"


# ── Diagnóstico ───────────────────────────────────────────────────────────────

def metricas_video(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
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


def data_range_disponivel() -> dict:
    """Retorna min/max de datas disponíveis no banco — útil para diagnóstico."""
    return query_one("""
        SELECT
            MIN(data_inicio) AS data_minima,
            MAX(data_fim)    AS data_maxima,
            COUNT(*)         AS total_linhas,
            COUNT(DISTINCT campanha) AS total_campanhas
        FROM campanhas_unificada
    """) or {}


# ── Consultas principais ──────────────────────────────────────────────────────

def listar_campanhas() -> list[dict]:
    """Lista todas as campanhas únicas com período de atividade."""
    return query("""
        SELECT
            campanha,
            MIN(data_inicio)                    AS primeira_data,
            MAX(data_fim)                       AS ultima_data,
            COUNT(*)                            AS dias_com_dados,
            ROUND(SUM(investimento), 2)         AS investimento_total
        FROM campanhas_unificada
        WHERE campanha IS NOT NULL AND campanha != ''
        GROUP BY campanha
        ORDER BY investimento_total DESC
    """)


def resumo_por_campanha(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Resumo agregado por campanha: investimento, compras, ROAS ponderado, CPC, CTR."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
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

    # Fallback: LIKE
    like_pattern = f"%{campanha}%"
    where_like, params_like = _build_where(
        "campanha LIKE ?",
        extra_params=[like_pattern],
        data_inicio=data_inicio,
        data_fim=data_fim,
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


AWARENESS_PATTERNS = ['tráfego', 'trafego', 'awareness', 'engajamento', 'alcance', 'visitas']


def top_campanhas_roas(
    limit:                   int = 10,
    data_inicio:             Optional[str] = None,
    data_fim:                Optional[str] = None,
    excluir_awareness:       bool = True,
    exigir_atividade_recente: bool = True,
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
) -> list[dict]:
    """Top N campanhas por volume de compras."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
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


def evolucao_diaria(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Evolução diária agregada (todas as campanhas ou uma específica)."""
    base = "campanha = ?" if campanha else None
    extra = [campanha] if campanha else []
    where, params = _build_where(
        base,
        extra_params=extra,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )
    return query(f"""
        SELECT
            data_inicio                            AS data,
            ROUND(SUM(investimento), 2)            AS investimento,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2)           AS receita,
            ROUND(SUM(COALESCE(compras, 0)), 0)                  AS compras,
            {_ROAS_EXPR}                                         AS roas,
            ROUND(SUM(impressoes), 0)                            AS impressoes,
            ROUND(SUM(COALESCE(cliques_link, 0)), 0)             AS cliques,
            ROUND(CAST(SUM(COALESCE(cliques_link, 0)) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4) AS ctr_medio
        FROM campanhas_unificada
        {where}
        GROUP BY data_inicio
        ORDER BY data_inicio ASC
    """, tuple(params))


def resumo_periodo(data_inicio: str, data_fim: str) -> dict:
    """
    Totais consolidados de um período.

    Retorna dict vazio se não há dados — inclui 'aviso' quando isso ocorre.
    """
    where, params = _build_where(None, data_inicio=data_inicio, data_fim=data_fim)
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


def alertas_roas_baixo(threshold: float = 1.0, janela_dias: int = 30) -> list[dict]:
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

    return query("""
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
          AND LOWER(campanha) NOT LIKE '%tr_fego%'
        GROUP BY campanha
        HAVING SUM(investimento) >= 100
           AND (
               SUM(valor_compras) = 0
               OR SUM(valor_compras) / NULLIF(SUM(investimento), 0) < ?
           )
        ORDER BY
            CASE WHEN SUM(valor_compras) = 0 THEN 0 ELSE 1 END ASC,
            SUM(valor_compras) / NULLIF(SUM(investimento), 0) ASC
    """, (threshold, data_max, janela_sql, threshold))


def comparar_campanhas(
    campanhas:   list[str],
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Compara múltiplas campanhas lado a lado."""
    if not campanhas:
        return []
    placeholders = ", ".join(["?"] * len(campanhas))
    where, params = _build_where(
        f"campanha IN ({placeholders})",
        extra_params=list(campanhas),
        data_inicio=data_inicio,
        data_fim=data_fim,
    )
    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(investimento), 2)          AS investimento,
            ROUND(SUM(valor_compras), 2)          AS receita,
            ROUND(SUM(compras), 0)                AS compras,
            {_ROAS_EXPR}                          AS roas,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc_medio,
            ROUND(AVG(ctr_link), 4)               AS ctr_medio,
            ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS cpa_ajustado,
            ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia_media,
            ROUND(SUM(impressoes), 0)             AS impressoes,
            ROUND(SUM(alcance), 0)                AS alcance,
            MIN(data_inicio)                      AS data_inicio,
            MAX(data_fim)                         AS data_fim
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        ORDER BY investimento DESC
    """, tuple(params))


def metricas_funil(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> dict:
    """Métricas de funil: impressões → cliques → carrinho → checkout → compra."""
    base = "campanha = ?" if campanha else None
    extra = [campanha] if campanha else []
    where, params = _build_where(
        base,
        extra_params=extra,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )
    return query_one(f"""
        SELECT
            ROUND(SUM(impressoes), 0)                        AS impressoes,
            ROUND(SUM(COALESCE(cliques_link, 0)), 0)         AS cliques,
            ROUND(SUM(visualizacoes_landing), 0)             AS landing_views,
            ROUND(SUM(views_conteudo), 0)                    AS views_conteudo,
            ROUND(SUM(COALESCE(adicoes_carrinho, 0)), 0)     AS adicoes_carrinho,
            ROUND(SUM(COALESCE(checkout_iniciado, 0)), 0)    AS checkout_iniciado,
            ROUND(SUM(COALESCE(compras, 0)), 0)              AS compras,
            ROUND(SUM(investimento), 2)                      AS investimento,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2)        AS receita
        FROM campanhas_unificada
        {where}
    """, tuple(params)) or {}


# ── helper interno ────────────────────────────────────────────────────────────

def _build_where(
    base_condition: Optional[str],
    extra_params:   list = None,
    data_inicio:    Optional[str] = None,
    data_fim:       Optional[str] = None,
) -> tuple[str, list]:
    """
    Constrói cláusula WHERE completa de forma segura.

    - base_condition: condição inicial (ex: "campanha IS NOT NULL", pode ser None)
    - extra_params: parâmetros posicionais correspondentes à base_condition
    - data_inicio / data_fim: filtros de período opcionais

    Retorna (where_clause_str, params_list).
    """
    conditions = []
    params = list(extra_params or [])

    if base_condition:
        conditions.append(base_condition)

    if data_inicio:
        conditions.append("data_inicio >= ?")
        params.append(data_inicio)
    if data_fim:
        conditions.append("data_fim <= ?")
        params.append(data_fim)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


# ── Novas consultas v3 ────────────────────────────────────────────────────────

def historico_mensal(
    campanha:    Optional[str] = None,
    meses:       int = 12,
) -> list[dict]:
    """
    Série histórica mensal: últimos N meses com totais e ROAS.
    Útil para visualizar sazonalidade e tendências de longo prazo.
    """
    import calendar as _cal
    from datetime import date as _date

    hoje = _date.today()
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
        where, params = _build_where(base, extra_params=extra, data_inicio=ini, data_fim=fim)

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


def top_campanhas_ctr(
    limit:       int = 10,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Top N campanhas por CTR (mínimo 1.000 impressões para evitar distorção)."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
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
) -> list[dict]:
    """Top N campanhas por menor CPM (mais eficiente em custo de exibição)."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
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
) -> list[dict]:
    """Top N campanhas por alcance total único."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
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
) -> list[dict]:
    """Top N campanhas por volume de cliques no link."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
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
) -> list[dict]:
    """Top N campanhas por receita total gerada."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        data_inicio=data_inicio,
        data_fim=data_fim,
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


def distribuicao_investimento(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
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


def campanhas_sem_conversao(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    invest_min:  float = 50.0,
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

    return query("""
        SELECT
            campanha,
            MAX(data_fim)                        AS ultima_atividade,
            ROUND(SUM(investimento), 2)          AS investimento_total,
            ROUND(SUM(COALESCE(compras, 0)), 0)  AS compras_total,
            {_ROAS}                              AS roas_historico,
            COUNT(DISTINCT data_inicio)          AS dias_com_dados
        FROM campanhas_unificada
        WHERE campanha IS NOT NULL AND campanha != ''
        GROUP BY campanha
        HAVING MAX(data_fim) < DATE(?, ?)
           AND SUM(investimento) >= 100
        ORDER BY ultima_atividade DESC
    """.replace("{_ROAS}", _ROAS_EXPR), (data_max, f"-{dias_sem_dados} days"))


def variacao_diaria_roas(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """
    Volatilidade do ROAS dia a dia.

    Retorna ROAS diário e variação percentual em relação ao dia anterior.
    Útil para identificar picos, quedas e instabilidades.
    """
    base  = "campanha = ?" if campanha else None
    extra = [campanha] if campanha else []
    where, params = _build_where(
        base,
        extra_params=extra,
        data_inicio=data_inicio,
        data_fim=data_fim,
    )
    return query(f"""
        SELECT
            data_inicio                         AS data,
            ROUND(SUM(investimento), 2)         AS investimento,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2) AS receita,
            ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4) AS roas,
            ROUND(
                (SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0)
                - LAG(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0))
                    OVER (ORDER BY data_inicio))
                / NULLIF(
                    LAG(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0))
                    OVER (ORDER BY data_inicio),
                    0
                ) * 100
            , 1) AS variacao_roas_pct,
            ROUND(SUM(COALESCE(compras, 0)), 0) AS compras
        FROM campanhas_unificada
        {where}
        GROUP BY data_inicio
        ORDER BY data_inicio ASC
    """, tuple(params))


def saturacao_frequencia(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    threshold:   float = 3.0,
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


def custo_funil_detalhado(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
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
            -- Visualização de conteúdo
            ROUND(SUM(COALESCE(views_conteudo, 0)), 0)     AS views_conteudo,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(views_conteudo, 0)), 0), 2) AS custo_por_view_conteudo,
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


def metricas_engajamento(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
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


def resumo_executivo(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
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
    totais    = resumo_periodo(data_inicio, data_fim) if (data_inicio and data_fim) else {}
    top_roas  = top_campanhas_roas(3, data_inicio, data_fim)
    top_comp  = top_campanhas_compras(3, data_inicio, data_fim)
    top_rec   = top_campanhas_receita(3, data_inicio, data_fim)
    alertas   = alertas_roas_baixo()
    sem_conv  = campanhas_sem_conversao(data_inicio, data_fim)
    distrib   = distribuicao_investimento(data_inicio, data_fim)

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
        "FROM campanhas WHERE data_inicio >= ? AND data_fim <= ?",
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
    from db.connection import get_conn
    import sqlite3

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
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
        ).fetchall()
        return [dict(r) for r in rows]
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
