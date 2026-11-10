-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_performance_completa` AS
SELECT
    a.cliente,
    a.mes,
    a.investimento_meta,
    a.investimento_google,
    ROUND(a.investimento_meta + a.investimento_google, 2)         AS investimento_total,
    a.compras_meta,
    a.compras_google,
    s.receita                                                     AS receita_shopify,
    s.pedidos,
    s.novos_clientes,
    s.ticket_medio,
    ROUND(s.receita / NULLIF(
        a.investimento_meta + a.investimento_google, 0), 2)       AS roas_real_total
FROM (
    SELECT
        cliente,
        DATE_TRUNC(data, MONTH)                                   AS mes,
        ROUND(SUM(CASE WHEN plataforma='meta'
                       THEN investimento * 1.1215 ELSE 0 END), 2) AS investimento_meta,
        ROUND(SUM(CASE WHEN plataforma='google'
                       THEN investimento ELSE 0 END), 2)          AS investimento_google,
        ROUND(SUM(CASE WHEN plataforma='meta'
                       THEN compras ELSE 0 END), 4)               AS compras_meta,
        ROUND(SUM(CASE WHEN plataforma='google'
                       THEN compras ELSE 0 END), 4)               AS compras_google
    FROM `guara-db.ingestao.ing_ads_diario`
    GROUP BY 1, 2
) a
LEFT JOIN (
    SELECT
        cliente,
        DATE_TRUNC(data, MONTH)                                   AS mes,
        ROUND(SUM(receita), 2)                                    AS receita,
        SUM(pedidos)                                              AS pedidos,
        SUM(novos_clientes)                                       AS novos_clientes,
        ROUND(AVG(ticket_medio), 2)                               AS ticket_medio
    FROM `guara-db.ingestao.ing_shopify_resumo_diario`
    GROUP BY 1, 2
) s USING (cliente, mes);
