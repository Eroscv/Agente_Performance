-- Tipo de cliente da Shopify por pedido (Source Of): 'novo' (customer.orders_count = 1) ou
-- 'recorrente', o mesmo critério do resumo diário oficial (shopify_diario.novos_clientes).
-- Preenchido por scripts/sync_pedidos_diario.py; pedido sem customer fica nulo.
ALTER TABLE `guara-db.guara_performance.pedidos_diario`
  ADD COLUMN IF NOT EXISTS tipo_cliente STRING,
  ADD COLUMN IF NOT EXISTS customer_orders_count INT64;
