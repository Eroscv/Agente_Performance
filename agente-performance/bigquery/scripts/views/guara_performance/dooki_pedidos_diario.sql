-- Resumo diário da Yampi da POD por loja. Mantém o nome e as colunas da tabela antiga
-- dooki_pedidos_diario (parada em 05/11/2026 e ainda consultada à mão), agora calculada da
-- tabela por pedido, então está sempre atual.
--   fonte           → 'dooki_' + loja (dooki_pod_kombucha, dooki_poddi)
--   pedidos/receita → só pedidos pagos; receita COM frete, como na tabela antiga
--   receita_sem_frete (coluna nova) → a mesma base do gold (fato_pedidos_pod)
-- A linha antiga com fonte 'dooki' era uma cópia da loja pod_kombucha e não existe mais.
CREATE OR REPLACE VIEW `guara-db.guara_performance.dooki_pedidos_diario` AS
SELECT
  data,
  CAST(SUM(IF(pago, valor_pago, 0)) AS NUMERIC) AS receita,
  COUNTIF(pago) AS pedidos,
  CAST(ROUND(SAFE_DIVIDE(SUM(IF(pago, valor_pago, 0)), COUNTIF(pago)), 2) AS NUMERIC) AS ticket_medio,
  COUNT(DISTINCT IF(pago, customer_id, NULL)) AS clientes_unicos,
  CONCAT('dooki_', loja) AS fonte,
  MAX(atualizado_em) AS atualizado_em,
  CAST(SUM(IF(pago, receita_sem_frete, 0)) AS NUMERIC) AS receita_sem_frete
FROM `guara-db.ingestao.ing_yampi_pedidos`
GROUP BY data, loja;
