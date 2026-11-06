-- =============================================================================
-- Stored Procedure: Load Gold Layer (Silver -> Gold)
-- =============================================================================
-- Schema: `guara-db.correlacao` (gold)
-- Source: `guara-db.ingestao` (silver) only. Gold never reads bronze.
--
-- Purpose:
--   fato_pedidos  — incremental: MERGEs new paid orders from
--                   ing_shopify_pedidos (silver). Derives `canal`
--                   from utm_source. Never UPDATEs existing rows — matches
--                   the original historical correction that can't be
--                   reverse-engineered from the schema alone (see
--                   bigquery/docs/data_catalog_gold.md).
--   fato_pedidos (poddi) — depois de fato_pedidos_pod, insere os pedidos pagos da poddi
--                   com a classificação de fato_pedidos_pod.
--   fato_pedidos_pod — full rebuild, um pedido por linha, metodologia do Colab
--                   da Bia (tutorial "Como calculamos o CAC — Pod Kombucha"):
--                   pod.com.br = pedidos Shopify (ing_shopify_pedidos) ligados aos
--                   pedidos da Yampi pod-kombucha por order_name =
--                   shopify_order_number; a Yampi só dá o status de pagamento
--                   (não soma receita por cima). Sem par na Yampi, vale
--                   financial_status = paid da Shopify. poddi.com.br = Yampi
--                   poddi, receita própria. Receita sem frete, canal por
--                   utm_source (fn_canal_pod), cliente novo por regra do Colab.
--   fato_performance_diaria — full rebuild joining ing_ads_diario (anuncio
--                   level only) with fato_pedidos (Shopify clients, exceto POD)
--                   and fato_pedidos_pod (POD).
--
-- Usage:
--   CALL `guara-db.correlacao.load_gold`();
--
-- Naming: `load_<layer>` per bigquery/docs/naming_conventions.md.
-- Run `CALL ingestao.load_silver()` first so ing_ads_diario is current.
-- =============================================================================

-- Canal do pedido pela utm_source, regra do Colab (tutorial, seção 5).
-- Substring, como no Colab: "ig"/"fb" casam em qualquer parte do texto.
-- Fonte desconhecida (ex: klaviyo, manychat) cai em Orgânico/Direto ('direto').
CREATE OR REPLACE FUNCTION `guara-db.correlacao.fn_canal_pod`(utm_source STRING)
RETURNS STRING AS (
  CASE
    WHEN utm_source IS NULL OR TRIM(utm_source) = '' THEN 'direto'
    WHEN REGEXP_CONTAINS(LOWER(utm_source), r'facebook|meta|fb|ig|instagram') THEN 'meta'
    WHEN LOWER(utm_source) LIKE '%google%' THEN 'google'
    WHEN REGEXP_CONTAINS(LOWER(utm_source), r'revi|email|sms|shopper') THEN 'outros'
    ELSE 'direto'
  END
);

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
    FROM `guara-db.ingestao.ing_shopify_pedidos`
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

  -- fato_pedidos_pod (full rebuild) — ver cabeçalho
  CREATE OR REPLACE TABLE `guara-db.correlacao.fato_pedidos_pod`
  PARTITION BY data_brt
  CLUSTER BY loja
  AS
  WITH hist AS (
    SELECT customer_id, email, primeiro_pedido_em
    FROM `guara-db.ingestao.ing_shopify_clientes_historico`
    WHERE cliente = 'pod_kombucha'
  ),
  hist_cliente AS (
    SELECT customer_id, MIN(primeiro_pedido_em) AS primeiro FROM hist GROUP BY customer_id
  ),
  hist_email AS (
    SELECT email, MIN(primeiro_pedido_em) AS primeiro FROM hist WHERE email IS NOT NULL GROUP BY email
  ),
  shopify AS (
    SELECT order_id, order_name, data_brt, created_at_utc, status, valor_pago, frete,
           current_subtotal_price, customer_id, customer_email,
           utm_source, utm_medium, utm_campaign, utm_content
    FROM `guara-db.ingestao.ing_shopify_pedidos`
    WHERE cliente = 'pod_kombucha'
      -- Pedido Shopify sem order_name, dentro do período coberto pela Yampi, é linha
      -- obsoleta: a Shopify parou de devolvê-lo como pago (cancelado/estornado depois)
      -- e ele não pareia com a Yampi. Antes da cobertura da Yampi, vale o histórico.
      AND (order_name IS NOT NULL
           OR data_brt < (SELECT MIN(data) FROM `guara-db.ingestao.ing_yampi_pedidos`
                          WHERE loja = 'pod_kombucha'))
  ),
  yampi_pod AS (
    -- Uma linha da Yampi pod-kombucha por pedido Shopify (dedup defensivo).
    SELECT * EXCEPT(rn) FROM (
      SELECT *, ROW_NUMBER() OVER (
        PARTITION BY shopify_order_number ORDER BY pago DESC, created_at_utc DESC
      ) AS rn
      FROM `guara-db.ingestao.ing_yampi_pedidos`
      WHERE loja = 'pod_kombucha' AND shopify_order_number IS NOT NULL
    ) WHERE rn = 1
  ),
  pod_com_br AS (
    SELECT
      'pod.com.br' AS loja,
      COALESCE(s.order_id, CONCAT('yampi-', y.order_id)) AS order_id,
      COALESCE(s.order_name, CONCAT('#', y.shopify_order_number)) AS order_name,
      COALESCE(s.data_brt, y.data) AS data_brt,
      COALESCE(s.created_at_utc, y.created_at_utc) AS created_at_utc,
      y.status AS status_atual,
      IF(y.order_id IS NOT NULL, 'dooki', 'shopify_fallback') AS fonte_pagamento,
      IF(y.order_id IS NOT NULL, y.pago, s.status = 'paid') AS pago,
      COALESCE(s.current_subtotal_price, s.valor_pago - s.frete, y.receita_sem_frete) AS receita_sem_frete,
      COALESCE(s.valor_pago, y.valor_pago) AS total_price,
      COALESCE(s.frete, y.frete) AS frete,
      s.customer_id AS customer_id,
      COALESCE(s.customer_email, y.customer_email) AS customer_email,
      -- Canal/UTM: Shopify quando há pedido Shopify (é o que o Colab usa), senão Yampi.
      IF(s.order_id IS NOT NULL, s.utm_source,   y.utm_source)   AS utm_source,
      IF(s.order_id IS NOT NULL, s.utm_medium,   y.utm_medium)   AS utm_medium,
      IF(s.order_id IS NOT NULL, s.utm_campaign, y.utm_campaign) AS utm_campaign,
      IF(s.order_id IS NOT NULL, s.utm_content,  y.utm_content)  AS utm_content
    FROM shopify s
    FULL OUTER JOIN yampi_pod y
      ON REGEXP_REPLACE(s.order_name, r'^#', '') = y.shopify_order_number
  ),
  pod_com_br_novo AS (
    SELECT p.*,
      COALESCE(hc.primeiro, he.primeiro) AS primeiro_pedido_shopify,
      MIN(IF(p.pago, p.created_at_utc, NULL)) OVER (PARTITION BY p.customer_id) AS primeira_paga_cliente
    FROM pod_com_br p
    LEFT JOIN hist_cliente hc ON hc.customer_id = p.customer_id
    LEFT JOIN hist_email   he ON he.email = p.customer_email
  ),
  poddi AS (
    SELECT y.*,
      he.primeiro AS primeiro_pod_com_br,
      MIN(y.created_at_utc) OVER (PARTITION BY y.customer_email) AS primeiro_poddi,
      MIN(IF(y.pago, y.created_at_utc, NULL)) OVER (PARTITION BY y.customer_email) AS primeira_paga_poddi
    FROM `guara-db.ingestao.ing_yampi_pedidos` y
    LEFT JOIN hist_email he ON he.email = y.customer_email
    WHERE y.loja = 'poddi' AND y.data >= DATE '2026-10-01'
  )
  SELECT loja, order_id, order_name, data_brt, created_at_utc, status_atual, fonte_pagamento, pago,
         receita_sem_frete, total_price, frete, customer_id, customer_email,
         -- Cliente novo pod.com.br: primeiro pedido da vida na Shopify (tolerância de 5 min).
         primeiro_pedido_shopify IS NOT NULL
           AND ABS(TIMESTAMP_DIFF(created_at_utc, primeiro_pedido_shopify, MINUTE)) <= 5 AS novo_cliente,
         `guara-db.correlacao.fn_canal_pod`(utm_source) AS canal,
         utm_source, utm_medium, utm_campaign, utm_content,
         -- Primeira compra PAGA: o primeiro pedido pago do cliente, mesmo que o primeiro pedido
         -- da vida dele tenha sido cancelado (aí novo_cliente=FALSE e esta coluna=TRUE).
         -- Cliente cujo primeiro pedido é anterior à janela observável fica FALSE.
         pago AND customer_id IS NOT NULL AND created_at_utc = primeira_paga_cliente
           AND primeiro_pedido_shopify > TIMESTAMP '1970-01-01' AS primeira_compra_paga
  FROM pod_com_br_novo
  UNION ALL
  SELECT
    'poddi.com.br', CONCAT('poddi-', order_id), CONCAT('poddi-', order_id), data, created_at_utc,
    status, 'yampi_nova', pago, receita_sem_frete, valor_pago, frete, customer_id, customer_email,
    -- Cliente novo poddi: e-mail preenchido, nunca comprou no pod.com.br antes (5 min) e
    -- é o primeiro pedido desse e-mail na poddi desde 01/10 (qualquer status, sem
    -- tolerância: um pedido cancelado minutos antes já torna o seguinte recorrente).
    customer_email IS NOT NULL
      AND (primeiro_pod_com_br IS NULL
           OR primeiro_pod_com_br >= TIMESTAMP_SUB(created_at_utc, INTERVAL 5 MINUTE))
      AND created_at_utc = primeiro_poddi,
    `guara-db.correlacao.fn_canal_pod`(utm_source),
    utm_source, utm_medium, utm_campaign, utm_content,
    pago AND customer_email IS NOT NULL AND created_at_utc = primeira_paga_poddi
      AND (primeiro_pod_com_br IS NULL
           OR primeiro_pod_com_br >= TIMESTAMP_SUB(created_at_utc, INTERVAL 5 MINUTE))
  FROM poddi;

  -- fato_pedidos: pedidos pagos da poddi (Yampi). Mesmo MERGE insert-only da Shopify,
  -- mas a classificação (pago, canal, cliente novo) vem de fato_pedidos_pod para não
  -- existir duas regras. Isto inclui a POD inteira em fato_pedidos; para as métricas da
  -- POD (receita sem frete, Yampi pod-kombucha, status atual) use fato_pedidos_pod.
  MERGE `guara-db.correlacao.fato_pedidos` T
  USING (
    SELECT
      p.order_id,
      'pod_kombucha' AS cliente_id,
      FALSE AS cliente_nao_canonizado,
      p.data_brt,
      p.created_at_utc,
      'paid' AS status,
      p.total_price AS valor_pago,
      y.desconto,
      p.frete,
      p.novo_cliente,
      p.novo_cliente AS novo_cliente_raw,
      p.customer_id,
      p.utm_source,
      p.utm_campaign,
      p.utm_content,
      'yampi' AS source_name,
      p.canal
    FROM `guara-db.correlacao.fato_pedidos_pod` p
    JOIN `guara-db.ingestao.ing_yampi_pedidos` y
      ON y.loja = 'poddi' AND CONCAT('poddi-', y.order_id) = p.order_id
    WHERE p.loja = 'poddi.com.br' AND p.pago
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
    WHERE entra_no_total
    GROUP BY cliente, data, plataforma
  ),
  -- Source Of / Botica (a POD usa fato_pedidos_pod, metodologia do Colab).
  -- Fonte: resumo diário da Shopify (ing_shopify_resumo_diario), que bate com a API da Shopify:
  -- receita paga = subtotal sem frete, novos clientes e canais da própria sincronização.
  -- Os pedidos/receita CRIADOS só existem por dia (sem canal); aqui são repartidos entre os canais
  -- na proporção dos pedidos pagos do dia, então a soma dos canais é exata e a divisão por canal é
  -- aproximada. Dia sem resumo (histórico antigo) cai em fato_pedidos.
  resumo AS (
    SELECT cliente AS cliente_id, data, pedidos, CAST(receita AS FLOAT64) AS receita,
           pedidos_meta, CAST(receita_meta AS FLOAT64) AS receita_meta, novos_meta,
           pedidos_google, CAST(receita_google AS FLOAT64) AS receita_google, novos_google,
           pedidos_outros, CAST(receita_outros AS FLOAT64) AS receita_outros, novos_outros,
           pedidos_criados, CAST(receita_criada AS FLOAT64) AS receita_criada,
           COALESCE(SAFE_DIVIDE(pedidos_meta, pedidos), 0) AS sh_meta,
           COALESCE(SAFE_DIVIDE(pedidos_google, pedidos), 0) AS sh_google
    FROM `guara-db.ingestao.ing_shopify_resumo_diario`
    WHERE cliente != 'pod_kombucha'
  ),
  pedidos_shopify AS (
    SELECT cliente_id, data, 'meta' AS canal, pedidos_meta AS pedidos, receita_meta AS receita,
           novos_meta AS novos_clientes,
           CAST(ROUND(sh_meta * pedidos_criados) AS INT64) AS pedidos_criados,
           sh_meta * receita_criada AS receita_criada
    FROM resumo
    UNION ALL
    SELECT cliente_id, data, 'google', pedidos_google, receita_google, novos_google,
           CAST(ROUND(sh_google * pedidos_criados) AS INT64), sh_google * receita_criada
    FROM resumo
    UNION ALL
    SELECT cliente_id, data, 'outros', pedidos_outros, receita_outros, novos_outros,
           pedidos_criados - CAST(ROUND(sh_meta * pedidos_criados) AS INT64)
                           - CAST(ROUND(sh_google * pedidos_criados) AS INT64),
           receita_criada - sh_meta * receita_criada - sh_google * receita_criada
    FROM resumo
    UNION ALL
    SELECT fp.cliente_id, fp.data_brt, fp.canal, COUNT(*), SUM(fp.valor_pago), COUNTIF(fp.novo_cliente),
           CAST(NULL AS INT64), CAST(NULL AS FLOAT64)
    FROM `guara-db.correlacao.fato_pedidos` fp
    WHERE fp.status = 'paid' AND fp.cliente_id != 'pod_kombucha'
      AND NOT EXISTS (SELECT 1 FROM resumo r WHERE r.cliente_id = fp.cliente_id AND r.data = fp.data_brt)
    GROUP BY fp.cliente_id, fp.data_brt, fp.canal
  ),
  pedidos_pod AS (
    SELECT 'pod_kombucha' AS cliente_id, data_brt AS data, canal,
      COUNTIF(pago) AS pedidos,
      SUM(IF(pago, receita_sem_frete, 0)) AS receita,
      COUNTIF(pago AND novo_cliente) AS novos_clientes,
      COUNT(*) AS pedidos_criados,
      SUM(receita_sem_frete) AS receita_criada
    FROM `guara-db.correlacao.fato_pedidos_pod`
    GROUP BY data_brt, canal
  ),
  pedidos AS (
    SELECT * FROM pedidos_shopify WHERE pedidos > 0 OR pedidos_criados > 0 OR pedidos_criados IS NULL
    UNION ALL SELECT * FROM pedidos_pod
  )
  SELECT COALESCE(a.cliente_id, p.cliente_id) AS cliente_id, COALESCE(a.data, p.data) AS data,
    COALESCE(a.canal, p.canal) AS canal, COALESCE(a.investimento, 0) AS investimento,
    a.cliques_link, a.impressoes, COALESCE(p.pedidos, 0) AS pedidos, COALESCE(p.receita, 0) AS receita,
    COALESCE(p.novos_clientes, 0) AS novos_clientes,
    SAFE_DIVIDE(COALESCE(p.receita, 0), COALESCE(a.investimento, 0)) AS roas_real,
    -- Meta com imposto de 12,15% (Google sem imposto). Use esta coluna, não um fator próprio.
    CASE WHEN COALESCE(a.canal, p.canal) = 'meta'
         THEN COALESCE(a.investimento, 0) * 1.1215
         ELSE COALESCE(a.investimento, 0) END AS investimento_ajustado,
    p.pedidos_criados, p.receita_criada
  FROM ads a
  FULL OUTER JOIN pedidos p ON a.cliente_id = p.cliente_id AND a.data = p.data AND a.canal = p.canal;

  -- fato_cac (full rebuild): CAC mensal = investimento ajustado (Meta x1,1215, Google x1)
  -- dividido por clientes novos pela regra de cliente novo de cada cliente. Por ser derivado de
  -- fato_performance_diaria, acompanha qualquer mudança nas regras na mesma carga.
  -- cac_primeira_compra_paga (só POD) usa a primeira compra paga em vez do primeiro pedido.
  CREATE OR REPLACE TABLE `guara-db.correlacao.fato_cac`
  AS
  WITH mes AS (
    SELECT cliente_id,
           DATE_TRUNC(data, MONTH) AS periodo_inicio,
           MAX(data) AS periodo_fim,
           SUM(novos_clientes) AS novos_clientes,
           SUM(investimento_ajustado) AS investimento_ajustado
    FROM `guara-db.correlacao.fato_performance_diaria`
    WHERE data >= DATE '2026-09-01'
    GROUP BY cliente_id, periodo_inicio
  ),
  pago AS (
    SELECT 'pod_kombucha' AS cliente_id, DATE_TRUNC(data_brt, MONTH) AS periodo_inicio,
           COUNTIF(primeira_compra_paga) AS novos_primeira_compra_paga
    FROM `guara-db.correlacao.fato_pedidos_pod`
    GROUP BY periodo_inicio
  )
  SELECT m.cliente_id, m.periodo_inicio, m.periodo_fim, m.novos_clientes,
         m.investimento_ajustado AS investimento_total,
         ROUND(SAFE_DIVIDE(m.investimento_ajustado, m.novos_clientes), 2) AS cac,
         p.novos_primeira_compra_paga,
         ROUND(SAFE_DIVIDE(m.investimento_ajustado, p.novos_primeira_compra_paga), 2) AS cac_primeira_compra_paga,
         'fato_performance_diaria: investimento_ajustado / novos_clientes do mês' AS fonte_logica_novos_clientes,
         CURRENT_TIMESTAMP() AS calculado_em
  FROM mes m
  LEFT JOIN pago p USING (cliente_id, periodo_inicio);


END;
