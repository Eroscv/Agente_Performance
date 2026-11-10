-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.correlacao.v_dash_shopify_corrigida` AS
SELECT cliente_id, data, SUM(pedidos) AS pedidos, SUM(receita) AS receita, SUM(novos_clientes) AS novos_clientes
FROM `guara-db.correlacao.fato_performance_diaria`
GROUP BY 1,2;
