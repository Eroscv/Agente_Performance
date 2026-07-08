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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
        FROM campanhas
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
