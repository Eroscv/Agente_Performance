"""
DEFASADO: as views em produção estão versionadas em bigquery/scripts/views/. Este gerador
não reproduz as definições atuais e rodá-lo desfaz correções já aplicadas. Não executar.
"""
"""
Cria/recria as views específicas por página do dashboard no BigQuery.

Views criadas:
  v_dash_overview    → Overview: KPIs totais, série temporal (cliente/plataforma/data)
  v_dash_sources     → Sources: Amount spent e CPC por Source (plataforma) e Data
  v_dash_campaigns   → Campaigns: Amount spent, Clicks, CPC por Campaign/Campaign type
  v_dash_shopify     → Receita real (Shopify), dedupada, para blocos de loja

Alterações (23/10/2026):
  - receita renomeado para receita_plataforma em todas as views (evitar confusão com Shopify)
  - campaign_type: N/A e Google Ads sem objetivo → 'Google'
  - CPM, CPA, CTR, CPC removidos das views (criar como campo calculado no Data Studio)

Campos calculados no Data Studio:
  CPM  = SUM(investimento)          / SUM(impressoes) * 1000
  CPA  = SUM(investimento_ajustado) / SUM(compras)
  CTR  = SUM(cliques)               / SUM(impressoes) * 100
  CPC  = SUM(amount_spent)          / SUM(cliques)
  ROAS = SUM(receita_plataforma)    / SUM(investimento_ajustado)

Regras:
  - Todas as views usam SOMENTE ads_diario como fonte de métricas de anúncio.
  - Meta recebe fator fiscal ×1.1215 em investimento_ajustado / amount_spent.
  - CPM, CPA, CPC, ROAS são campos calculados (divisão) — nunca somá-los.
  - v_todos_dados existe apenas para auditoria, nunca para somar métricas.

Uso:
    GOOGLE_APPLICATION_CREDENTIALS=credentials/bq-service-account.json \\
    python3 scripts/create_bq_views_dashboard_pages.py
"""
import os
from google.cloud import bigquery

P = os.environ.get("BQ_PROJECT", "guara-db")
D = os.environ.get("BQ_DATASET", "guara_performance")
bq = bigquery.Client(project=P)

# Bloco de deduplicação reutilizável
_DEDUP = f"""
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
"""

# Expressão de investimento ajustado (Meta ×1.1215, Google inalterado)
_INV_AJ = "CASE WHEN plataforma = 'meta' THEN investimento * 1.1215 ELSE investimento END"

# Mapa de nome canônico de cliente
_CLIENTE_NOME = """
    CASE cliente
        WHEN 'source_of'    THEN 'Source OF'
        WHEN 'pod_kombucha' THEN 'POD Kombucha'
        WHEN 'botica'       THEN 'Botica'
        ELSE cliente
    END
"""

views = {}

# ── v_dash_overview ──────────────────────────────────────────────────────────
views["v_dash_overview"] = f"""
CREATE OR REPLACE VIEW `{P}.{D}.v_dash_overview` AS
-- Dashboard › Overview
-- Granularidade: cliente / plataforma / data
-- CPM, CPA, ROAS são campos calculados — nunca somá-los diretamente.
SELECT
    cliente,
    {_CLIENTE_NOME}                                                     AS cliente_nome,
    plataforma,
    data,

    ROUND(SUM(investimento), 2)                                         AS investimento,
    ROUND(SUM({_INV_AJ}), 2)                                            AS investimento_ajustado,
    ROUND(SUM(compras), 0)                                              AS compras,
    ROUND(SUM(receita), 2)                                              AS receita_plataforma,

    ROUND(SUM(receita) / NULLIF(SUM({_INV_AJ}), 0), 4)                 AS roas,
    ROUND(SUM({_INV_AJ}) / NULLIF(SUM(compras), 0), 2)                 AS cpa,

    ROUND(SUM(impressoes), 0)                                           AS impressoes,
    ROUND(SUM(alcance), 0)                                              AS alcance,
    ROUND(SUM(cliques_link), 0)                                         AS cliques,
    ROUND(SUM(cliques_link) / NULLIF(SUM(impressoes), 0) * 100, 4)     AS ctr,
    ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2)    AS cpm,

    ROUND(SUM(adicoes_carrinho), 0)                                     AS adicoes_carrinho,
    ROUND(SUM(checkout_iniciado), 0)                                    AS checkout_iniciado,
    ROUND(SUM(video_3s), 0)                                             AS video_3s,
    ROUND(SUM(thruplays), 0)                                            AS thruplays,
    ROUND(AVG(frequencia), 2)                                           AS frequencia

FROM ({_DEDUP})
GROUP BY 1, 2, 3, 4
"""

# ── v_dash_sources ───────────────────────────────────────────────────────────
views["v_dash_sources"] = f"""
CREATE OR REPLACE VIEW `{P}.{D}.v_dash_sources` AS
-- Dashboard › Sources
-- source = plataforma (meta | google)
-- CPC = amount_spent ÷ cliques  (campo calculado — nunca somar)
SELECT
    cliente,
    {_CLIENTE_NOME}                                                     AS cliente_nome,
    plataforma                                                          AS source,
    data,

    ROUND(SUM(investimento), 2)                                         AS investimento,
    ROUND(SUM({_INV_AJ}), 2)                                            AS amount_spent,
    ROUND(SUM(compras), 0)                                              AS compras,
    ROUND(SUM(receita), 2)                                              AS receita_plataforma,
    ROUND(SUM(impressoes), 0)                                           AS impressoes,
    ROUND(SUM(cliques_link), 0)                                         AS cliques,

    ROUND(SUM({_INV_AJ}) / NULLIF(SUM(cliques_link), 0), 2)            AS cpc,
    ROUND(SUM(cliques_link) / NULLIF(SUM(impressoes), 0) * 100, 4)     AS ctr,
    ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2)    AS cpm,
    ROUND(SUM(receita) / NULLIF(SUM({_INV_AJ}), 0), 4)                 AS roas,
    ROUND(SUM({_INV_AJ}) / NULLIF(SUM(compras), 0), 2)                 AS cpa

FROM ({_DEDUP})
GROUP BY 1, 2, 3, 4
"""

# ── v_dash_campaigns ─────────────────────────────────────────────────────────
views["v_dash_campaigns"] = f"""
CREATE OR REPLACE VIEW `{P}.{D}.v_dash_campaigns` AS
-- Dashboard › Campaigns
-- campaign_type = objetivo (não o campo "compras")
-- CPC = amount_spent ÷ cliques  (campo calculado — nunca somar)
SELECT
    cliente,
    {_CLIENTE_NOME}                                                     AS cliente_nome,
    plataforma,
    data,
    campanha,
    campaign_id,
    -- N/A = Google Ads (sem objetivo) → renomeado para 'Google'
    CASE
        WHEN plataforma = 'google' THEN 'Google'
        WHEN objetivo IS NULL      THEN 'Google'
        WHEN objetivo = ''         THEN 'Google'
        ELSE objetivo
    END                                                                 AS campaign_type,
    status,

    ROUND(SUM(investimento), 2)                                         AS investimento,
    ROUND(SUM({_INV_AJ}), 2)                                            AS amount_spent,
    ROUND(SUM(compras), 0)                                              AS compras,
    ROUND(SUM(receita), 2)                                              AS receita_plataforma,
    ROUND(SUM(impressoes), 0)                                           AS impressoes,
    ROUND(SUM(cliques_link), 0)                                         AS cliques,

    ROUND(SUM({_INV_AJ}) / NULLIF(SUM(cliques_link), 0), 2)            AS cpc,
    ROUND(SUM(receita) / NULLIF(SUM({_INV_AJ}), 0), 4)                 AS roas,
    ROUND(SUM({_INV_AJ}) / NULLIF(SUM(compras), 0), 2)                 AS cpa,
    ROUND(SUM(cliques_link) / NULLIF(SUM(impressoes), 0) * 100, 4)     AS ctr,
    ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2)    AS cpm,

    ROUND(SUM(adicoes_carrinho), 0)                                     AS adicoes_carrinho,
    ROUND(SUM(checkout_iniciado), 0)                                    AS checkout_iniciado,
    ROUND(SUM(video_3s), 0)                                             AS video_3s,
    ROUND(SUM(thruplays), 0)                                            AS thruplays

FROM ({_DEDUP})
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
"""

# ── v_dash_shopify ───────────────────────────────────────────────────────────
views["v_dash_shopify"] = f"""
CREATE OR REPLACE VIEW `{P}.{D}.v_dash_shopify` AS
-- Dashboard › blocos de receita real (Shopify)
-- Use para receita, pedidos, novos clientes, ticket médio.
-- NÃO misturar com views de anúncio para métricas de investimento/ROAS.
SELECT * EXCEPT(rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY cliente, data
            ORDER BY criado_em DESC
        ) AS rn
    FROM `{P}.ingestao.ing_shopify_resumo_diario`
)
WHERE rn = 1
"""

for name, ddl in views.items():
    print(f"Criando {name}...", end=" ", flush=True)
    job = bq.query(ddl)
    job.result()
    print("✅")

print(f"\n✅ {len(views)} views de dashboard criadas/atualizadas!")
print("\nMapeamento dashboard → view:")
print("  Overview (KPIs, gráfico de série)      → v_dash_overview")
print("  Sources (Amount spent, CPC por Source) → v_dash_sources")
print("  Campaigns (Campaign type, CPC)         → v_dash_campaigns")
print("  Blocos de receita/pedidos (Shopify)    → v_dash_shopify")
print("\nCampos calculados (nunca somar):")
print("  cpa   = investimento_ajustado ÷ compras")
print("  cpm   = investimento ÷ impressoes × 1000")
print("  cpc   = amount_spent ÷ cliques")
print("  roas  = receita ÷ investimento_ajustado")
print("  ctr   = cliques ÷ impressoes × 100")
