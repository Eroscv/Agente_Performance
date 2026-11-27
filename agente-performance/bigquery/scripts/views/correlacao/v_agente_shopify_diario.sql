-- Consumo do Agente de Performance: mesma forma da view local shopify_receita_diaria
-- (db/duck.py): resumo diário da Shopify da Source Of, sem coluna de cliente.
CREATE OR REPLACE VIEW `guara-db.correlacao.v_agente_shopify_diario` AS
SELECT
  data,
  CAST(receita AS FLOAT64) AS receita,
  CAST(COALESCE(frete, 0) AS FLOAT64) AS frete,
  pedidos,
  novos_clientes,
  recompradores,
  CAST(ticket_medio AS FLOAT64) AS ticket_medio,
  CAST(total_desconto AS FLOAT64) AS total_desconto,
  pedidos_gt300 AS pedidos_acima_300,
  CAST(COALESCE(receita_criada, 0) AS FLOAT64) AS receita_criada,
  COALESCE(pedidos_criados, 0) AS pedidos_criados,
  COALESCE(pedidos_meta, 0) AS pedidos_meta,
  CAST(COALESCE(receita_meta, 0) AS FLOAT64) AS receita_meta,
  COALESCE(novos_meta, 0) AS novos_meta,
  COALESCE(pedidos_google, 0) AS pedidos_google,
  CAST(COALESCE(receita_google, 0) AS FLOAT64) AS receita_google,
  COALESCE(novos_google, 0) AS novos_google,
  COALESCE(pedidos_outros, 0) AS pedidos_outros,
  CAST(COALESCE(receita_outros, 0) AS FLOAT64) AS receita_outros,
  COALESCE(novos_outros, 0) AS novos_outros,
  CAST(COALESCE(gross_sales, 0) AS FLOAT64) AS gross_sales,
  COALESCE(pedidos_gt300, 0) AS pedidos_gt300,
  sessoes,
  sessoes_com_carrinho,
  sessoes_checkout,
  sessoes_compra,
  taxa_conversao,
  is_bot_suspect
FROM `guara-db.ingestao.ing_shopify_resumo_diario`
WHERE cliente = 'source_of';
