-- =============================================================================
-- Stored Procedure: Load Silver Layer (Bronze -> Silver)
-- =============================================================================
-- Schema: `guara-db.ingestao` (silver)
-- Source: `guara-db.guara_performance` (bronze)
--
-- Purpose:
--   Refreshes every ing_<entidade> table in `ingestao` from its matching
--   bronze table in `guara_performance`. Each table is a full CREATE OR
--   REPLACE (not incremental) because the bronze sources are small/cheap to
--   rescan daily — this mirrors the DataWithBaraa reference project's
--   bronze/silver load pattern (truncate + full reload), adapted to
--   BigQuery (no BULK INSERT / TRUNCATE+INSERT, just CREATE OR REPLACE
--   TABLE ... AS SELECT, which is atomic and cheaper on BigQuery).
--
--   ing_ads_diario is the one exception: it also derives the `nivel`
--   column (anuncio / campanha) used throughout the gold layer and the
--   dashboards, so its logic lives here too instead of being duplicated.
--
-- Usage:
--   CALL `guara-db.ingestao.load_silver`();
--
-- Naming: `load_<layer>` per bigquery/docs/naming_conventions.md.
-- =============================================================================

CREATE OR REPLACE PROCEDURE `guara-db.ingestao.load_silver`()
BEGIN

  -- ing_shopify_pedidos <- guara_performance.pedidos_diario
  -- (DROP+CREATE, not CREATE OR REPLACE: the table existed without a
  -- partition before this procedure, and BigQuery refuses to change an
  -- existing table's partitioning spec in place)
  DROP TABLE IF EXISTS `guara-db.ingestao.ing_shopify_pedidos`;
  CREATE TABLE `guara-db.ingestao.ing_shopify_pedidos`
  PARTITION BY data_brt
  CLUSTER BY cliente
  AS
  SELECT * FROM `guara-db.guara_performance.pedidos_diario`;

  -- ing_shopify_resumo_diario <- guara_performance.shopify_diario
  DROP TABLE IF EXISTS `guara-db.ingestao.ing_shopify_resumo_diario`;
  CREATE TABLE `guara-db.ingestao.ing_shopify_resumo_diario`
  PARTITION BY data
  CLUSTER BY cliente
  AS
  SELECT * FROM `guara-db.guara_performance.shopify_diario`;

  -- ing_checkout_abandonado_diario <- guara_performance.abandoned_checkout_daily
  DROP TABLE IF EXISTS `guara-db.ingestao.ing_checkout_abandonado_diario`;
  CREATE TABLE `guara-db.ingestao.ing_checkout_abandonado_diario`
  PARTITION BY data
  CLUSTER BY cliente
  AS
  SELECT * FROM `guara-db.guara_performance.abandoned_checkout_daily`;

  -- ing_checkout_abandonado_detalhe <- guara_performance.abandoned_checkout_detail
  DROP TABLE IF EXISTS `guara-db.ingestao.ing_checkout_abandonado_detalhe`;
  CREATE TABLE `guara-db.ingestao.ing_checkout_abandonado_detalhe`
  PARTITION BY data
  CLUSTER BY cliente
  AS
  SELECT * FROM `guara-db.guara_performance.abandoned_checkout_detail`;

  -- ing_meta_campaign_totals <- guara_performance.meta_campaign_totals
  CREATE OR REPLACE TABLE `guara-db.ingestao.ing_meta_campaign_totals`
  AS
  SELECT * FROM `guara-db.guara_performance.meta_campaign_totals`;

  -- ing_novos_clientes_semanal <- guara_performance.novos_clientes_semanal
  CREATE OR REPLACE TABLE `guara-db.ingestao.ing_novos_clientes_semanal`
  AS
  SELECT * FROM `guara-db.guara_performance.novos_clientes_semanal`;

  -- ing_ads_diario <- guara_performance.ads_diario (+ nivel derivado)
  CREATE OR REPLACE TABLE `guara-db.ingestao.ing_ads_diario`
  PARTITION BY data
  CLUSTER BY cliente, plataforma
  AS
  SELECT *,
    CASE
      WHEN ad_id IS NOT NULL THEN 'anuncio'
      WHEN ad_id IS NULL AND adset_id IS NOT NULL THEN 'adset'
      ELSE 'campanha'
    END AS nivel
  FROM `guara-db.guara_performance.ads_diario`;

END;
