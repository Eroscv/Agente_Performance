-- =============================================================================
-- Stored Procedures: Load Silver Layer (Bronze -> Silver)
-- =============================================================================
-- Schema: `guara-db.ingestao` (silver)
-- Source: `guara-db.guara_performance` (bronze)
--
-- Purpose:
--   Mantém cada ing_<entidade> em `ingestao` igual à sua tabela bronze já
--   LIMPA. A silver nunca é recriada: cada tabela recebe um MERGE idempotente
--   pela chave natural (insere o que é novo, atualiza só o que mudou, remove
--   o que saiu do bronze). Rodar duas vezes seguidas não altera nada.
--
--   A limpeza mora aqui, não no gold:
--     - texto: trim, e-mail/utm_source/utm_medium em minúsculas, vazio vira NULL
--     - ing_ads_diario: `nivel` derivado; cliques totais menores que cliques no
--       link (a fonte não coletou) viram NULL e a linha fica marcada em
--       dq_cliques_todos_ausente
--     - ing_yampi_pedidos: sem o `novo_cliente` do bronze (regra é do gold)
--     - ing_tickets: um ticket por ticket_id (a versão mais recente)
--
--   sp_upsert é o único mecanismo de carga: monta o MERGE a partir das colunas
--   da tabela destino, então uma coluna nova só precisa entrar no DDL
--   (bigquery/scripts/silver/ddl_silver.sql) e na consulta de origem.
--
-- Usage:
--   CALL `guara-db.ingestao.load_silver`();
--
-- Naming: `load_<layer>` per bigquery/docs/naming_conventions.md.
-- =============================================================================

CREATE OR REPLACE PROCEDURE `guara-db.ingestao.sp_upsert`(
  alvo STRING, fonte_sql STRING, chaves ARRAY<STRING>)
BEGIN
  DECLARE colunas ARRAY<STRING>;
  DECLARE linhas_fonte, afetadas INT64;
  DECLARE lista_colunas, condicao_chave, atribuicoes STRING;

  EXECUTE IMMEDIATE FORMAT('CREATE OR REPLACE TEMP TABLE _fonte AS %s', fonte_sql);
  SET linhas_fonte = (SELECT COUNT(*) FROM _fonte);
  -- Fonte vazia quase sempre é falha de carga no bronze: não apaga a silver por isso.
  IF linhas_fonte = 0 THEN
    RAISE USING MESSAGE = FORMAT('load_silver: fonte de %s veio vazia, nada foi alterado', alvo);
  END IF;

  SET colunas = (
    SELECT ARRAY_AGG(column_name ORDER BY ordinal_position)
    FROM `guara-db.ingestao.INFORMATION_SCHEMA.COLUMNS`
    WHERE table_name = alvo);
  SET lista_colunas = (SELECT STRING_AGG(FORMAT('`%s`', c), ', ') FROM UNNEST(colunas) c);
  -- Chave composta comparada como um valor só: null-safe e aceita pelo BigQuery
  -- em MERGE/anti-join (que exigem igualdade entre as duas tabelas).
  SET condicao_chave = (
    SELECT FORMAT('TO_JSON_STRING(STRUCT(%s)) = TO_JSON_STRING(STRUCT(%s))',
                  STRING_AGG(FORMAT('T.`%s`', k), ', '), STRING_AGG(FORMAT('S.`%s`', k), ', '))
    FROM UNNEST(chaves) k);
  SET atribuicoes = (
    SELECT STRING_AGG(FORMAT('T.`%s` = S.`%s`', c, c), ', ')
    FROM UNNEST(colunas) c WHERE c NOT IN UNNEST(chaves));

  EXECUTE IMMEDIATE FORMAT("""
    MERGE `guara-db.ingestao.%s` T
    USING (SELECT %s FROM _fonte) S
    ON %s
    WHEN MATCHED AND TO_JSON_STRING(T) != TO_JSON_STRING(S) THEN UPDATE SET %s
    WHEN NOT MATCHED THEN INSERT (%s) VALUES (%s)
  """, alvo, lista_colunas, condicao_chave, atribuicoes, lista_colunas,
       (SELECT STRING_AGG(FORMAT('S.`%s`', c), ', ') FROM UNNEST(colunas) c));
  SET afetadas = @@row_count;

  -- O MERGE não remove com comparação null-safe: linhas que saíram do bronze saem aqui.
  EXECUTE IMMEDIATE FORMAT("""
    DELETE FROM `guara-db.ingestao.%s` T
    WHERE NOT EXISTS (SELECT 1 FROM _fonte S WHERE %s)
  """, alvo, condicao_chave);
  SET afetadas = afetadas + @@row_count;

  INSERT INTO `guara-db.ingestao.log_cargas`
  VALUES (CURRENT_TIMESTAMP(), 'silver', alvo, linhas_fonte, afetadas);
END;

CREATE OR REPLACE PROCEDURE `guara-db.ingestao.load_silver`()
BEGIN

  CALL `guara-db.ingestao.sp_upsert`('ing_shopify_pedidos', r'''
    SELECT * REPLACE(
      LOWER(TRIM(NULLIF(utm_source, '')))     AS utm_source,
      LOWER(TRIM(NULLIF(utm_medium, '')))     AS utm_medium,
      LOWER(TRIM(NULLIF(customer_email, ''))) AS customer_email)
    FROM `guara-db.guara_performance.pedidos_diario`
  ''', ['cliente', 'order_id']);

  CALL `guara-db.ingestao.sp_upsert`('ing_shopify_resumo_diario', r'''
    SELECT * FROM `guara-db.guara_performance.shopify_diario`
  ''', ['cliente', 'data']);

  CALL `guara-db.ingestao.sp_upsert`('ing_checkout_abandonado_diario', r'''
    SELECT * FROM `guara-db.guara_performance.abandoned_checkout_daily`
  ''', ['cliente', 'data', 'utm_source_norm']);

  CALL `guara-db.ingestao.sp_upsert`('ing_checkout_abandonado_detalhe', r'''
    SELECT * REPLACE(LOWER(TRIM(NULLIF(email, ''))) AS email)
    FROM `guara-db.guara_performance.abandoned_checkout_detail`
  ''', ['cliente', 'checkout_id']);

  CALL `guara-db.ingestao.sp_upsert`('ing_meta_campaign_totals', r'''
    SELECT * FROM `guara-db.guara_performance.meta_campaign_totals`
  ''', ['cliente', 'data_inicio', 'data_fim', 'campaign_id', 'account_id']);

  CALL `guara-db.ingestao.sp_upsert`('ing_novos_clientes_semanal', r'''
    SELECT * FROM `guara-db.guara_performance.novos_clientes_semanal`
  ''', ['cliente', 'semana_inicio']);

  CALL `guara-db.ingestao.sp_upsert`('ing_ads_diario', r'''
    SELECT * REPLACE(
      IF(cliques_todos IS NULL OR cliques_todos < cliques_link, NULL, cliques_todos) AS cliques_todos),
      CASE
        WHEN ad_id IS NOT NULL THEN 'anuncio'
        WHEN adset_id IS NOT NULL THEN 'adset'
        ELSE 'campanha'
      END AS nivel,
      cliques_todos IS NULL OR cliques_todos < cliques_link AS dq_cliques_todos_ausente
    FROM `guara-db.guara_performance.ads_diario`
  ''', ['cliente', 'plataforma', 'data', 'campaign_id', 'adset_id', 'ad_id', 'account_id']);

  CALL `guara-db.ingestao.sp_upsert`('ing_yampi_pedidos', r'''
    SELECT * EXCEPT(novo_cliente) REPLACE(
      LOWER(TRIM(NULLIF(utm_source, '')))     AS utm_source,
      LOWER(TRIM(NULLIF(utm_medium, '')))     AS utm_medium,
      LOWER(TRIM(NULLIF(customer_email, ''))) AS customer_email)
    FROM `guara-db.guara_performance.dooki_pedidos`
  ''', ['loja', 'order_id']);

  CALL `guara-db.ingestao.sp_upsert`('ing_shopify_clientes_historico', r'''
    SELECT * REPLACE(LOWER(TRIM(NULLIF(email, ''))) AS email)
    FROM `guara-db.guara_performance.shopify_clientes_historico`
  ''', ['cliente', 'customer_id', 'email']);

  CALL `guara-db.ingestao.sp_upsert`('ing_tickets', r'''
    SELECT * EXCEPT(rn) FROM (
      SELECT *, ROW_NUMBER() OVER (PARTITION BY ticket_id ORDER BY atualizado_em DESC) AS rn
      FROM `guara-db.guara_performance.tickets_alerta`
    ) WHERE rn = 1
  ''', ['ticket_id']);

  CALL `guara-db.ingestao.sp_upsert`('ing_metricas_sla', r'''
    SELECT * FROM `guara-db.guara_performance.metricas_sla`
  ''', ['gerado_em', 'janela_dias', 'tipo']);

END;
