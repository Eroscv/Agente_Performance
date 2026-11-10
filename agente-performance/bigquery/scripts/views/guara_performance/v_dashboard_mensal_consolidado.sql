-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_dashboard_mensal_consolidado` AS
WITH ads AS (
    SELECT
        cliente,
        plataforma,
        DATE_TRUNC(data, MONTH)                                         AS mes,
        ROUND(SUM(investimento_ajustado), 2)                            AS investimento,
        ROUND(SUM(receita), 2)                                          AS receita_plataforma,
        ROUND(SUM(compras), 4)                                          AS compras,
        ROUND(SUM(impressoes), 0)                                       AS impressoes,
        ROUND(SUM(cliques_todos), 0)                                     AS cliques
    FROM `guara-db.guara_performance.v_dashboard_diario`
    GROUP BY 1, 2, 3
),
shopify AS (
    -- Deduplica shopify_diario (mantém linha mais recente por cliente/data)
    SELECT * EXCEPT(rn)
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (PARTITION BY cliente, data ORDER BY criado_em DESC) AS rn
        FROM `guara-db.ingestao.ing_shopify_resumo_diario`
    )
    WHERE rn = 1
),
shopify_mes AS (
    SELECT
        cliente,
        DATE_TRUNC(data, MONTH)                                         AS mes,
        ROUND(SUM(receita), 2)                                          AS receita_shopify,
        SUM(pedidos)                                                    AS pedidos,
        SUM(novos_clientes)                                             AS novos_clientes,
        SUM(recompradores)                                              AS recompradores,
        ROUND(AVG(ticket_medio), 2)                                     AS ticket_medio,
        ROUND(SUM(total_desconto), 2)                                   AS total_desconto
    FROM shopify
    GROUP BY 1, 2
),
base AS (
    SELECT
        a.cliente,
        a.plataforma,
        a.mes,
        a.investimento,
        a.receita_plataforma,
        a.compras,
        a.impressoes,
        a.cliques,
        ROUND(a.receita_plataforma / NULLIF(a.investimento, 0), 4)      AS roas_plataforma,
        ROUND(a.investimento / NULLIF(a.compras, 0), 2)                 AS cpa,
        s.receita_shopify,
        s.pedidos,
        s.novos_clientes,
        s.recompradores,
        s.ticket_medio,
        s.total_desconto,
        -- ROAS real = receita Shopify ÷ investimento total (todos os canais daquele cliente/mês)
        ROUND(s.receita_shopify / NULLIF(
            SUM(a.investimento) OVER (PARTITION BY a.cliente, a.mes), 0), 4) AS roas_real_shopify
    FROM ads a
    LEFT JOIN shopify_mes s USING (cliente, mes)
)
SELECT
    *,
    LAG(investimento)    OVER w                                         AS invest_mes_ant,
    LAG(receita_shopify) OVER w                                         AS receita_mes_ant,
    LAG(roas_real_shopify) OVER w                                       AS roas_mes_ant,
    ROUND((investimento - LAG(investimento) OVER w)
          / NULLIF(LAG(investimento) OVER w, 0) * 100, 1)              AS var_invest_pct,
    ROUND((receita_shopify - LAG(receita_shopify) OVER w)
          / NULLIF(LAG(receita_shopify) OVER w, 0) * 100, 1)           AS var_receita_pct,
    ROUND((roas_real_shopify - LAG(roas_real_shopify) OVER w)
          / NULLIF(LAG(roas_real_shopify) OVER w, 0) * 100, 1)         AS var_roas_pct
FROM base
WINDOW w AS (PARTITION BY cliente, plataforma ORDER BY mes)
ORDER BY cliente, plataforma, mes DESC;
