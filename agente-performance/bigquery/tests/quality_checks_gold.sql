-- =============================================================================
-- Quality Checks: Gold Layer (correlacao)
-- =============================================================================
-- Each SELECT below should return ZERO rows. Run after every
-- `CALL correlacao.load_gold()`.
-- =============================================================================

-- 1. fato_pedidos: duplicate (order_id, cliente_id) — the MERGE key must be unique
SELECT order_id, cliente_id, COUNT(*) AS linhas
FROM `guara-db.correlacao.fato_pedidos`
GROUP BY order_id, cliente_id
HAVING COUNT(*) > 1;

-- 2. fato_pedidos: canal must never be NULL (the CASE in load_gold() always
--    resolves to meta/google/direto/outros)
SELECT *
FROM `guara-db.correlacao.fato_pedidos`
WHERE canal IS NULL;

-- 3. fato_pedidos: cliente_id not present in dim_cliente (orphaned client —
--    either a new client missing from dim_cliente, or a canonicalization bug)
SELECT DISTINCT fp.cliente_id
FROM `guara-db.correlacao.fato_pedidos` fp
LEFT JOIN `guara-db.correlacao.dim_cliente` dc USING (cliente_id)
WHERE dc.cliente_id IS NULL;

-- 4. fato_performance_diaria: negative investimento or receita (data bug
--    upstream, should never happen)
SELECT *
FROM `guara-db.correlacao.fato_performance_diaria`
WHERE investimento < 0 OR receita < 0;

-- 5. fato_performance_diaria: cliente_id not present in dim_cliente
SELECT DISTINCT fpd.cliente_id
FROM `guara-db.correlacao.fato_performance_diaria` fpd
LEFT JOIN `guara-db.correlacao.dim_cliente` dc USING (cliente_id)
WHERE dc.cliente_id IS NULL;

-- 6. fato_performance_diaria: duplicate (cliente_id, data, canal) — the
--    FULL OUTER JOIN key in load_gold() must be unique per row
SELECT cliente_id, data, canal, COUNT(*) AS linhas
FROM `guara-db.correlacao.fato_performance_diaria`
GROUP BY cliente_id, data, canal
HAVING COUNT(*) > 1;

-- 7. fato_pedidos_pod: pedido duplicado (um pedido por loja + order_id)
SELECT loja, order_id, COUNT(*) AS linhas
FROM `guara-db.correlacao.fato_pedidos_pod`
GROUP BY loja, order_id
HAVING COUNT(*) > 1;

-- 8. fato_pedidos_pod: pedido pod.com.br contado 2x (mesmo order_name) — a Yampi
--    pod-kombucha são os mesmos pedidos da Shopify e não pode somar por cima
SELECT order_name, COUNT(*) AS linhas
FROM `guara-db.correlacao.fato_pedidos_pod`
WHERE loja = 'pod.com.br'
GROUP BY order_name
HAVING COUNT(*) > 1;

-- 9. fato_pedidos_pod: canal fora do vocabulário (meta/google/outros/direto) ou nulo
SELECT *
FROM `guara-db.correlacao.fato_pedidos_pod`
WHERE canal IS NULL OR canal NOT IN ('meta', 'google', 'outros', 'direto');

-- 10. fato_performance_diaria: o canal 'yampi_checkout' foi aposentado (a Yampi
--     entra nos canais meta/google/outros/direto pelo utm_source)
SELECT *
FROM `guara-db.correlacao.fato_performance_diaria`
WHERE canal = 'yampi_checkout';

-- 11. fato_pedidos_pod: pedido pago sem receita (Yampi sem par e sem valor)
SELECT *
FROM `guara-db.correlacao.fato_pedidos_pod`
WHERE pago AND receita_sem_frete IS NULL;
