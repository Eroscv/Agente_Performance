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
