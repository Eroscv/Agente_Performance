-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_novos_clientes_semanal` AS
SELECT
    cliente,
    DATE_TRUNC(data, WEEK(MONDAY))                          AS semana_inicio,
    DATE_ADD(DATE_TRUNC(data, WEEK(MONDAY)), INTERVAL 6 DAY) AS semana_fim,
    SUM(novos_clientes)                                     AS novos_clientes,
    SUM(recompradores)                                      AS recompradores,
    SUM(pedidos)                                            AS pedidos_total,
    ROUND(SUM(novos_clientes) / NULLIF(SUM(pedidos), 0) * 100, 2) AS pct_novos,
    ROUND(SUM(recompradores)  / NULLIF(SUM(pedidos), 0) * 100, 2) AS pct_recompra,
    SUM(novos_meta)                                         AS novos_meta,
    SUM(novos_google)                                       AS novos_google,
    SUM(novos_outros)                                       AS novos_outros,
    ROUND(SUM(novos_meta)    / NULLIF(SUM(novos_clientes), 0) * 100, 2) AS pct_novos_meta,
    ROUND(SUM(novos_google)  / NULLIF(SUM(novos_clientes), 0) * 100, 2) AS pct_novos_google,
    ROUND(SUM(novos_outros)  / NULLIF(SUM(novos_clientes), 0) * 100, 2) AS pct_novos_outros
FROM `guara-db.ingestao.ing_shopify_resumo_diario`
GROUP BY cliente, semana_inicio, semana_fim
ORDER BY cliente, semana_inicio DESC;
