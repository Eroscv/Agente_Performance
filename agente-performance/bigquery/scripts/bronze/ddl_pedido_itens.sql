-- =============================================================================
-- Bronze: estado e itens (SKU) dos pedidos da Shopify
-- =============================================================================
-- Aditivo e idempotente. Carregado por scripts/sync_pedidos_diario.py.
-- Só a UF do endereço de entrega é guardada (nunca o endereço completo).
-- =============================================================================

ALTER TABLE `guara-db.guara_performance.pedidos_diario`
  ADD COLUMN IF NOT EXISTS estado STRING;  -- UF (shipping_address.province_code)

-- Um item de pedido por linha. receita_item = preço × quantidade − desconto do item.
CREATE TABLE IF NOT EXISTS `guara-db.guara_performance.pedido_itens` (
  cliente       STRING NOT NULL,
  order_id      STRING NOT NULL,
  line_item_id  STRING NOT NULL,
  data_brt      DATE,
  sku           STRING,
  produto       STRING,
  variacao      STRING,
  quantidade    INT64,
  receita_item  FLOAT64,
  criado_em     TIMESTAMP
)
PARTITION BY data_brt
CLUSTER BY cliente;
