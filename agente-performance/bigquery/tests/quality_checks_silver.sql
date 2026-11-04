-- =============================================================================
-- Quality Checks: Silver Layer (bronze vs. ingestao)
-- =============================================================================
-- Purpose:
--   Confirms every ing_<entidade> table in `ingestao` is in sync with its
--   bronze source in `guara_performance`. This is exactly the check that
--   found 4 silver tables silently stale (up to 14 days behind) during the
--   2026-11-04 medallion audit — run it after every `CALL
--   ingestao.load_silver()`, or periodically to catch future drift if the
--   procedure stops running.
--
-- Expected result: ZERO rows. Any row returned means that silver table has
-- drifted from its bronze source (either a row-count mismatch or a stale
-- MAX(date)) and `CALL guara-db.ingestao.load_silver()` needs to run.
-- =============================================================================

WITH checks AS (
  SELECT 'ing_shopify_pedidos' AS tabela,
    (SELECT COUNT(*) FROM `guara-db.guara_performance.pedidos_diario`) AS bronze_linhas,
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_shopify_pedidos`) AS silver_linhas,
    (SELECT MAX(data_brt) FROM `guara-db.guara_performance.pedidos_diario`) AS bronze_max_data,
    (SELECT MAX(data_brt) FROM `guara-db.ingestao.ing_shopify_pedidos`) AS silver_max_data

  UNION ALL
  SELECT 'ing_shopify_resumo_diario',
    (SELECT COUNT(*) FROM `guara-db.guara_performance.shopify_diario`),
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_shopify_resumo_diario`),
    (SELECT MAX(data) FROM `guara-db.guara_performance.shopify_diario`),
    (SELECT MAX(data) FROM `guara-db.ingestao.ing_shopify_resumo_diario`)

  UNION ALL
  SELECT 'ing_checkout_abandonado_diario',
    (SELECT COUNT(*) FROM `guara-db.guara_performance.abandoned_checkout_daily`),
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_checkout_abandonado_diario`),
    (SELECT MAX(data) FROM `guara-db.guara_performance.abandoned_checkout_daily`),
    (SELECT MAX(data) FROM `guara-db.ingestao.ing_checkout_abandonado_diario`)

  UNION ALL
  SELECT 'ing_checkout_abandonado_detalhe',
    (SELECT COUNT(*) FROM `guara-db.guara_performance.abandoned_checkout_detail`),
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_checkout_abandonado_detalhe`),
    (SELECT MAX(data) FROM `guara-db.guara_performance.abandoned_checkout_detail`),
    (SELECT MAX(data) FROM `guara-db.ingestao.ing_checkout_abandonado_detalhe`)

  UNION ALL
  SELECT 'ing_meta_campaign_totals',
    (SELECT COUNT(*) FROM `guara-db.guara_performance.meta_campaign_totals`),
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_meta_campaign_totals`),
    (SELECT MAX(data_fim) FROM `guara-db.guara_performance.meta_campaign_totals`),
    (SELECT MAX(data_fim) FROM `guara-db.ingestao.ing_meta_campaign_totals`)

  UNION ALL
  SELECT 'ing_novos_clientes_semanal',
    (SELECT COUNT(*) FROM `guara-db.guara_performance.novos_clientes_semanal`),
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_novos_clientes_semanal`),
    (SELECT MAX(semana_fim) FROM `guara-db.guara_performance.novos_clientes_semanal`),
    (SELECT MAX(semana_fim) FROM `guara-db.ingestao.ing_novos_clientes_semanal`)

  UNION ALL
  SELECT 'ing_ads_diario',
    (SELECT COUNT(*) FROM `guara-db.guara_performance.ads_diario`),
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_ads_diario`),
    (SELECT MAX(data) FROM `guara-db.guara_performance.ads_diario`),
    (SELECT MAX(data) FROM `guara-db.ingestao.ing_ads_diario`)
)
SELECT *,
  bronze_linhas - silver_linhas AS diff_linhas,
  DATE_DIFF(bronze_max_data, silver_max_data, DAY) AS dias_defasado
FROM checks
WHERE bronze_linhas != silver_linhas OR bronze_max_data != silver_max_data;
