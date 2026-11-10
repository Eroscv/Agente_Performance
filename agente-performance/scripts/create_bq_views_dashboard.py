"""
DEFASADO: as views em produção estão versionadas em bigquery/scripts/views/. Este gerador
não reproduz as definições atuais e rodá-lo desfaz correções já aplicadas. Não executar.
"""
"""
Cria/recria as views analíticas para dashboards de performance no BigQuery.

Views criadas:
  v_dashboard_diario              → granularidade por campanha/adset/ad (dedupado, Meta ×1.1215)
  v_dashboard_diario_consolidado  → série temporal diária por cliente+plataforma
  v_dashboard_mensal_consolidado  → KPIs mensais com ROAS real (Shopify) + variação MoM

REGRA: nunca usar v_todos_dados para somar métricas — ela é só para auditoria.

Uso:
    GOOGLE_APPLICATION_CREDENTIALS=credentials/bq-service-account.json \\
    python3 scripts/create_bq_views_dashboard.py
"""
import os
from google.cloud import bigquery

P = os.environ.get("BQ_PROJECT", "guara-db")
D = os.environ.get("BQ_DATASET", "guara_performance")
bq = bigquery.Client(project=P)

views = {}

# ── v_dashboard_diario ───────────────────────────────────────────────────────
# Fonte base: ads_diario dedupado + ajuste fiscal Meta (×1.1215).
# Granularidade: cliente / plataforma / data / campanha / adset / ad.
views["v_dashboard_diario"] = f"""
CREATE OR REPLACE VIEW `{P}.{D}.v_dashboard_diario` AS
-- ⚠️  FONTE ÚNICA para dashboards de performance de anúncios.
-- Deduplica ads_diario (1 linha por cliente/plataforma/data/campanha/adset/ad)
-- e aplica fator fiscal Meta (×1.1215).
-- NÃO somar com meta_campaign_totals, shopify_diario ou pedidos_diario.
WITH dedup AS (
    SELECT * EXCEPT(rn, nivel, dq_cliques_todos_ausente)
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY cliente, plataforma, data, campaign_id, adset_id, ad_id
                ORDER BY criado_em DESC
            ) AS rn
        FROM `{P}.ingestao.ing_ads_diario`
    )
    WHERE rn = 1
)
SELECT
    cliente,
    plataforma,
    data,
    campanha,
    campaign_id,
    adset_name,
    adset_id,
    ad_name,
    ad_id,
    objetivo,
    status,
    account_id,

    ROUND(investimento, 2)                                              AS investimento,
    ROUND(
        CASE WHEN plataforma = 'meta'
             THEN investimento * 1.1215
             ELSE investimento
        END, 2
    )                                                                   AS investimento_ajustado,

    compras,
    ROUND(receita, 2)                                                   AS receita,

    ROUND(
        receita / NULLIF(
            CASE WHEN plataforma = 'meta'
                 THEN investimento * 1.1215
                 ELSE investimento
            END, 0), 4
    )                                                                   AS roas_real,

    ROUND(
        CASE WHEN plataforma = 'meta'
             THEN investimento * 1.1215
             ELSE investimento
        END / NULLIF(compras, 0), 2
    )                                                                   AS cpa_real,

    impressoes,
    alcance,
    cliques_link,
    cliques_todos,
    ROUND(cliques_link / NULLIF(impressoes, 0) * 100, 4)                AS ctr,
    ROUND(investimento / NULLIF(impressoes, 0) * 1000, 2)               AS cpm_real,
    frequencia,

    video_3s,
    video_50pct,
    thruplays,
    adicoes_carrinho,
    checkout_iniciado,
    visualizacoes_landing,

    qualidade_ranking,
    engagement_ranking,
    conversion_ranking,

    criado_em
FROM dedup
"""

# ── v_dashboard_diario_consolidado ──────────────────────────────────────────
# Agrega por cliente/plataforma/data — sem granularidade de campanha.
# Use para gráficos de série temporal e KPIs diários.
views["v_dashboard_diario_consolidado"] = f"""
CREATE OR REPLACE VIEW `{P}.{D}.v_dashboard_diario_consolidado` AS
-- Série temporal diária por cliente+plataforma — sem duplicidades.
-- Use esta view para gráficos de linha e KPIs diários no dashboard.
SELECT
    cliente,
    plataforma,
    data,

    ROUND(SUM(investimento), 2)                                         AS investimento,
    ROUND(SUM(investimento_ajustado), 2)                                AS investimento_ajustado,
    ROUND(SUM(receita), 2)                                              AS receita,
    ROUND(SUM(compras), 4)                                              AS compras,

    ROUND(SUM(receita) / NULLIF(SUM(investimento_ajustado), 0), 4)      AS roas_real,
    ROUND(SUM(investimento_ajustado) / NULLIF(SUM(compras), 0), 2)      AS cpa_real,

    ROUND(SUM(impressoes), 0)                                           AS impressoes,
    ROUND(SUM(alcance), 0)                                              AS alcance,
    ROUND(SUM(cliques_link), 0)                                         AS cliques_link,
    ROUND(SUM(cliques_link) / NULLIF(SUM(impressoes), 0) * 100, 4)      AS ctr,
    ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2)     AS cpm,
    ROUND(AVG(frequencia), 2)                                           AS frequencia,

    ROUND(SUM(video_3s), 0)                                             AS video_3s,
    ROUND(SUM(thruplays), 0)                                            AS thruplays,
    ROUND(SUM(adicoes_carrinho), 0)                                     AS adicoes_carrinho,
    ROUND(SUM(checkout_iniciado), 0)                                    AS checkout_iniciado

FROM `{P}.{D}.v_dashboard_diario`
GROUP BY 1, 2, 3
ORDER BY cliente, plataforma, data
"""

# ── v_dashboard_mensal_consolidado ──────────────────────────────────────────
# KPIs mensais por cliente/plataforma com ROAS real (Shopify) e variação MoM.
# Investimento Meta já com fator fiscal ×1.1215.
views["v_dashboard_mensal_consolidado"] = f"""
CREATE OR REPLACE VIEW `{P}.{D}.v_dashboard_mensal_consolidado` AS
-- KPIs mensais por cliente+plataforma com ROAS real (Shopify) e variação MoM.
-- Investimento Meta com fator fiscal ×1.1215.
WITH ads AS (
    SELECT
        cliente,
        plataforma,
        DATE_TRUNC(data, MONTH)                                         AS mes,
        ROUND(SUM(investimento_ajustado), 2)                            AS investimento,
        ROUND(SUM(receita), 2)                                          AS receita_plataforma,
        ROUND(SUM(compras), 4)                                          AS compras,
        ROUND(SUM(impressoes), 0)                                       AS impressoes,
        ROUND(SUM(cliques_link), 0)                                     AS cliques
    FROM `{P}.{D}.v_dashboard_diario`
    GROUP BY 1, 2, 3
),
shopify AS (
    SELECT * EXCEPT(rn)
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (PARTITION BY cliente, data ORDER BY criado_em DESC) AS rn
        FROM `{P}.ingestao.ing_shopify_resumo_diario`
    )
    WHERE rn = 1
),
shopify_mes AS (
    SELECT
        cliente,
        DATE_TRUNC(data, MONTH)                                         AS mes,
        ROUND(SUM(receita), 2)                                          AS receita_shopify,
        SUM(pedidos)                                                    AS pedidos,
        SUM(novos_clientes)                                             AS novos_clientes,
        SUM(recompradores)                                              AS recompradores,
        ROUND(AVG(ticket_medio), 2)                                     AS ticket_medio,
        ROUND(SUM(total_desconto), 2)                                   AS total_desconto
    FROM shopify
    GROUP BY 1, 2
),
base AS (
    SELECT
        a.cliente,
        a.plataforma,
        a.mes,
        a.investimento,
        a.receita_plataforma,
        a.compras,
        a.impressoes,
        a.cliques,
        ROUND(a.receita_plataforma / NULLIF(a.investimento, 0), 4)      AS roas_plataforma,
        ROUND(a.investimento / NULLIF(a.compras, 0), 2)                 AS cpa,
        s.receita_shopify,
        s.pedidos,
        s.novos_clientes,
        s.recompradores,
        s.ticket_medio,
        s.total_desconto,
        ROUND(s.receita_shopify / NULLIF(
            SUM(a.investimento) OVER (PARTITION BY a.cliente, a.mes), 0), 4) AS roas_real_shopify
    FROM ads a
    LEFT JOIN shopify_mes s USING (cliente, mes)
)
SELECT
    *,
    LAG(investimento)       OVER w                                      AS invest_mes_ant,
    LAG(receita_shopify)    OVER w                                      AS receita_mes_ant,
    LAG(roas_real_shopify)  OVER w                                      AS roas_mes_ant,
    ROUND((investimento - LAG(investimento) OVER w)
          / NULLIF(LAG(investimento) OVER w, 0) * 100, 1)              AS var_invest_pct,
    ROUND((receita_shopify - LAG(receita_shopify) OVER w)
          / NULLIF(LAG(receita_shopify) OVER w, 0) * 100, 1)           AS var_receita_pct,
    ROUND((roas_real_shopify - LAG(roas_real_shopify) OVER w)
          / NULLIF(LAG(roas_real_shopify) OVER w, 0) * 100, 1)         AS var_roas_pct
FROM base
WINDOW w AS (PARTITION BY cliente, plataforma ORDER BY mes)
ORDER BY cliente, plataforma, mes DESC
"""

for name, ddl in views.items():
    print(f"Criando {name}...", end=" ", flush=True)
    job = bq.query(ddl)
    job.result()
    print("✅")

print(f"\n✅ {len(views)} views criadas/atualizadas com sucesso!")
