-- =============================================================================
-- Quality Checks: Silver Layer (bronze vs. ingestao)
-- =============================================================================
-- Rodar depois de todo `CALL ingestao.load_silver()`. Cada SELECT deve voltar
-- ZERO linhas.
-- =============================================================================

-- 1. Silver em dia com o bronze: mesma quantidade de linhas e mesma data máxima.
--    (ing_tickets é a exceção declarada: o bronze guarda versões repetidas do
--    ticket, a silver guarda a mais recente; ing_yampi_pedidos idem em linhas.)
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
  UNION ALL
  SELECT 'ing_yampi_pedidos',
    (SELECT COUNT(*) FROM `guara-db.guara_performance.dooki_pedidos`),
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_yampi_pedidos`),
    (SELECT MAX(data) FROM `guara-db.guara_performance.dooki_pedidos`),
    (SELECT MAX(data) FROM `guara-db.ingestao.ing_yampi_pedidos`)
  UNION ALL
  SELECT 'ing_shopify_clientes_historico',
    (SELECT COUNT(*) FROM `guara-db.guara_performance.shopify_clientes_historico`),
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_shopify_clientes_historico`),
    (SELECT DATE(MAX(atualizado_em)) FROM `guara-db.guara_performance.shopify_clientes_historico`),
    (SELECT DATE(MAX(atualizado_em)) FROM `guara-db.ingestao.ing_shopify_clientes_historico`)
  UNION ALL
  SELECT 'ing_metricas_sla',
    (SELECT COUNT(*) FROM `guara-db.guara_performance.metricas_sla`),
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_metricas_sla`),
    (SELECT DATE(MAX(gerado_em)) FROM `guara-db.guara_performance.metricas_sla`),
    (SELECT DATE(MAX(gerado_em)) FROM `guara-db.ingestao.ing_metricas_sla`)
  UNION ALL
  SELECT 'ing_tickets',
    (SELECT COUNT(DISTINCT ticket_id) FROM `guara-db.guara_performance.tickets_alerta`),
    (SELECT COUNT(*) FROM `guara-db.ingestao.ing_tickets`),
    (SELECT DATE(MAX(atualizado_em)) FROM `guara-db.guara_performance.tickets_alerta`),
    (SELECT DATE(MAX(atualizado_em)) FROM `guara-db.ingestao.ing_tickets`)
)
SELECT *, bronze_linhas - silver_linhas AS diff_linhas,
  DATE_DIFF(bronze_max_data, silver_max_data, DAY) AS dias_defasado
FROM checks
WHERE bronze_linhas != silver_linhas OR bronze_max_data != silver_max_data;

-- 2. Cliques totais nunca menores que cliques no link, salvo linha marcada em dq_cliques
--    (anomalia da própria Meta: clicks < inline_link_clicks).
SELECT plataforma, nivel, COUNT(*) AS linhas
FROM `guara-db.ingestao.ing_ads_diario`
WHERE cliques_todos < cliques_link AND dq_cliques IS NULL
GROUP BY plataforma, nivel;

-- 3. Meta sem total de cliques onde há investimento (o CPC do dashboard usa cliques_todos).
SELECT cliente, COUNT(*) AS linhas
FROM `guara-db.ingestao.ing_ads_diario`
WHERE plataforma = 'meta' AND nivel = 'anuncio' AND investimento > 0 AND cliques_todos IS NULL
GROUP BY cliente;

-- 4. Chave natural duplicada em qualquer tabela alimentada por MERGE.
SELECT 'ing_shopify_pedidos' AS tabela, cliente, order_id AS chave, COUNT(*) AS linhas
FROM `guara-db.ingestao.ing_shopify_pedidos` GROUP BY cliente, order_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'ing_yampi_pedidos', loja, order_id, COUNT(*)
FROM `guara-db.ingestao.ing_yampi_pedidos` GROUP BY loja, order_id HAVING COUNT(*) > 1
UNION ALL
SELECT 'ing_tickets', CAST(NULL AS STRING), ticket_id, COUNT(*)
FROM `guara-db.ingestao.ing_tickets` GROUP BY ticket_id HAVING COUNT(*) > 1;

-- 5. Texto sujo que a silver deveria ter limpo: e-mail/utm com maiúscula, espaço ou vazio.
SELECT 'ing_shopify_pedidos' AS tabela, COUNT(*) AS linhas
FROM `guara-db.ingestao.ing_shopify_pedidos`
WHERE utm_source != LOWER(TRIM(utm_source)) OR utm_source = ''
   OR customer_email != LOWER(TRIM(customer_email)) OR customer_email = ''
HAVING COUNT(*) > 0
UNION ALL
SELECT 'ing_yampi_pedidos', COUNT(*)
FROM `guara-db.ingestao.ing_yampi_pedidos`
WHERE utm_source != LOWER(TRIM(utm_source)) OR utm_source = ''
   OR customer_email != LOWER(TRIM(customer_email)) OR customer_email = ''
HAVING COUNT(*) > 0;

-- 6. A última carga da silver não pode ter mais de 12h (o cron roda a cada 6h).
SELECT MAX(executado_em) AS ultima_carga
FROM `guara-db.ingestao.log_cargas`
WHERE camada = 'silver'
HAVING MAX(executado_em) < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 12 HOUR);

-- 7. Todo pedido da Yampi dos últimos 30 dias tem a resposta original em raw_yampi_pedidos.
SELECT d.loja, d.order_id
FROM `guara-db.guara_performance.dooki_pedidos` d
WHERE d.data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
  AND NOT EXISTS (
    SELECT 1 FROM `guara-db.guara_performance.raw_yampi_pedidos` r
    WHERE r.loja = d.loja AND r.order_id = d.order_id);

-- 8. Mídia paga parada: Meta ou Google de um cliente sem dado nos últimos 3 dias
--    (token expirado ou sync falhando; o sync do cron pode ter registrado sucesso).
SELECT cliente, plataforma, MAX(data) AS ultima_data
FROM `guara-db.ingestao.ing_ads_diario`
WHERE (cliente, plataforma) IN (('pod_kombucha', 'meta'), ('pod_kombucha', 'google'),
                                ('source_of', 'meta'), ('source_of', 'google'))
GROUP BY cliente, plataforma
HAVING MAX(data) < DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY);
