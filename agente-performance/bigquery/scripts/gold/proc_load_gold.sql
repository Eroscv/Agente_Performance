-- =============================================================================
-- Stored Procedure: Load Gold Layer (Silver -> Gold)
-- =============================================================================
-- Schema: `guara-db.correlacao` (gold)
-- Source: `guara-db.ingestao` (silver) and `guara-db.guara_performance` (bronze,
--         for the Dooki/Yampi POD channel which has no silver mirror yet)
--
-- Purpose:
--   fato_pedidos  — incremental: MERGEs new paid orders from
--                   guara_performance.pedidos_diario (bronze; the silver
--                   mirror ing_shopify_pedidos is read-aligned with it,
--                   kept as bronze source here to preserve the existing
--                   incremental key/watermark behavior). Derives `canal`
--                   from utm_source. Never UPDATEs existing rows — matches
--                   the original historical correction that can't be
--                   reverse-engineered from the schema alone (see
--                   bigquery/docs/data_catalog_gold.md).
--   fato_performance_diaria — full rebuild joining ing_ads_diario (anuncio
--                   level only) with fato_pedidos (Shopify clients) and
--                   dooki_pedidos (POD's Yampi/Dooki checkout, por pedido —
--                   ver dooki/sync.py; status paid/on_carriage/delivered
--                   contam como receita confirmada).
--
-- Usage:
--   CALL `guara-db.correlacao.load_gold`();
--
-- Naming: `load_<layer>` per bigquery/docs/naming_conventions.md.
-- Run `CALL ingestao.load_silver()` first so ing_ads_diario is current.
-- =============================================================================

CREATE OR REPLACE PROCEDURE `guara-db.correlacao.load_gold`()
BEGIN

  -- fato_pedidos (incremental MERGE — insert-only, see header note on canal/novo_cliente)
  MERGE `guara-db.correlacao.fato_pedidos` T
  USING (
    SELECT
      order_id,
      cliente AS cliente_id,
      FALSE AS cliente_nao_canonizado,
      data_brt,
      created_at_utc,
      status,
      valor_pago,
      desconto,
      frete,
      novo_cliente,
      novo_cliente AS novo_cliente_raw,
      customer_id,
      utm_source,
      utm_campaign,
      utm_content,
      source_name,
      CASE
        WHEN utm_source IS NULL THEN 'direto'
        WHEN LOWER(utm_source) IN ('facebook', 'instagram', 'ig', 'igshopping', 'facebook ads', 'meta') THEN 'meta'
        WHEN LOWER(utm_source) = 'google' THEN 'google'
        ELSE 'outros'
      END AS canal
    FROM `guara-db.guara_performance.pedidos_diario`
    WHERE status = 'paid'
  ) S
  ON T.order_id = S.order_id AND T.cliente_id = S.cliente_id
  WHEN NOT MATCHED THEN
    INSERT (order_id, cliente_id, cliente_nao_canonizado, data_brt, created_at_utc,
            status, valor_pago, desconto, frete, novo_cliente, novo_cliente_raw,
            customer_id, utm_source, utm_campaign, utm_content, source_name, canal)
    VALUES (S.order_id, S.cliente_id, S.cliente_nao_canonizado, S.data_brt, S.created_at_utc,
            S.status, S.valor_pago, S.desconto, S.frete, S.novo_cliente, S.novo_cliente_raw,
            S.customer_id, S.utm_source, S.utm_campaign, S.utm_content, S.source_name, S.canal);

  -- fato_performance_diaria (full rebuild)
  CREATE OR REPLACE TABLE `guara-db.correlacao.fato_performance_diaria`
  PARTITION BY data
  CLUSTER BY cliente_id
  AS
  WITH ads AS (
    SELECT cliente AS cliente_id, data, plataforma AS canal,
      SUM(investimento) AS investimento, SUM(cliques_link) AS cliques_link, SUM(impressoes) AS impressoes
    FROM `guara-db.ingestao.ing_ads_diario`
    WHERE nivel = 'anuncio' OR plataforma != 'meta'
    GROUP BY cliente, data, plataforma
  ),
  pedidos_shopify AS (
    SELECT cliente_id, data_brt AS data, canal, COUNT(*) AS pedidos,
      SUM(valor_pago) AS receita, COUNTIF(novo_cliente) AS novos_clientes
    FROM `guara-db.correlacao.fato_pedidos`
    WHERE status = 'paid'
    GROUP BY cliente_id, data_brt, canal
  ),
  pedidos_yampi AS (
    -- Por pedido (dooki_pedidos) em vez de total agregado por dia — cada
    -- pedido é upsertado por (loja, order_id), então não há risco de
    -- duplicar ou perder pedidos ao somar as 3 lojas. Receita confirmada =
    -- status ainda não cancelado (paid/on_carriage/delivered).
    SELECT 'pod_kombucha' AS cliente_id, data, 'yampi_checkout' AS canal,
      COUNT(*) AS pedidos, SUM(valor_pago) AS receita,
      COUNTIF(novo_cliente) AS novos_clientes
    FROM `guara-db.guara_performance.dooki_pedidos`
    WHERE status IN ('paid', 'on_carriage', 'delivered')
    GROUP BY data
  ),
  pedidos AS (SELECT * FROM pedidos_shopify UNION ALL SELECT * FROM pedidos_yampi)
  SELECT COALESCE(a.cliente_id, p.cliente_id) AS cliente_id, COALESCE(a.data, p.data) AS data,
    COALESCE(a.canal, p.canal) AS canal, COALESCE(a.investimento, 0) AS investimento,
    a.cliques_link, a.impressoes, COALESCE(p.pedidos, 0) AS pedidos, COALESCE(p.receita, 0) AS receita,
    COALESCE(p.novos_clientes, 0) AS novos_clientes,
    SAFE_DIVIDE(COALESCE(p.receita, 0), COALESCE(a.investimento, 0)) AS roas_real
  FROM ads a
  FULL OUTER JOIN pedidos p ON a.cliente_id = p.cliente_id AND a.data = p.data AND a.canal = p.canal;

END;
