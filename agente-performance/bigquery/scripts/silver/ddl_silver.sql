-- =============================================================================
-- DDL Silver (ingestao): tabelas que o load_silver() só alimenta, nunca recria
-- =============================================================================
-- Rodar uma vez (idempotente: IF NOT EXISTS). O load_silver() faz MERGE nestas
-- tabelas; qualquer coluna nova entra aqui antes de entrar no MERGE.
-- =============================================================================

-- Yampi/Dooki das 3 lojas (poddi, pod-kombucha, pod8), um pedido por linha.
-- `novo_cliente` do bronze não vem: a regra de cliente novo é do gold.
CREATE TABLE IF NOT EXISTS `guara-db.ingestao.ing_yampi_pedidos`
PARTITION BY data
CLUSTER BY loja
AS
SELECT * EXCEPT(novo_cliente)
FROM `guara-db.guara_performance.dooki_pedidos`
WHERE FALSE;

-- Primeiro pedido por cliente da Shopify da POD (regra de cliente novo).
CREATE TABLE IF NOT EXISTS `guara-db.ingestao.ing_shopify_clientes_historico`
CLUSTER BY cliente
AS
SELECT * FROM `guara-db.guara_performance.shopify_clientes_historico`
WHERE FALSE;

-- entra_no_total: linha que soma nos totais (anúncio; ou campanha sem anúncio no mesmo dia).
-- Cliques totais ausentes na fonte (cliques_todos = 0 com cliques_link > 0) não
-- são zero: são "não coletado". A silver grava NULL e marca a linha aqui.
ALTER TABLE `guara-db.ingestao.ing_ads_diario`
  ADD COLUMN IF NOT EXISTS dq_cliques_todos_ausente BOOL,
  ADD COLUMN IF NOT EXISTS entra_no_total BOOL;

-- Controle de execução: uma linha por tabela a cada load_silver().
CREATE TABLE IF NOT EXISTS `guara-db.ingestao.log_cargas` (
  executado_em TIMESTAMP,
  camada       STRING,
  tabela       STRING,
  linhas_fonte INT64,
  linhas_afetadas INT64
)
PARTITION BY DATE(executado_em);
