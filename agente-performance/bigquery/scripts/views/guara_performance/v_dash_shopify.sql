-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_dash_shopify` AS
SELECT * EXCEPT(rn)
FROM (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY cliente, data
            ORDER BY criado_em DESC
        ) AS rn
    FROM `guara-db.ingestao.ing_shopify_resumo_diario`
)
WHERE rn = 1;
