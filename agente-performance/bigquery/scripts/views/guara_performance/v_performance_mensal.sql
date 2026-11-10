-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_performance_mensal` AS
WITH ads_mes AS (
                    SELECT
                        cliente,
                        DATE_TRUNC(data, MONTH)                                           AS mes,
                        ROUND(SUM(CASE WHEN plataforma = 'meta'
                                       THEN investimento * 1.1215 ELSE 0 END), 2)        AS investimento_meta,
                        ROUND(SUM(CASE WHEN plataforma = 'google'
                                       THEN investimento ELSE 0 END), 2)                  AS investimento_google,
                        ROUND(SUM(CASE WHEN plataforma = 'meta'
                                       THEN investimento * 1.1215
                                       ELSE investimento END), 2)                         AS investimento_total,
                        ROUND(SUM(CASE WHEN plataforma = 'meta'
                                      THEN compras ELSE 0 END), 4)                        AS compras_meta,
                        ROUND(SUM(CASE WHEN plataforma = 'google'
                                      THEN compras ELSE 0 END), 4)                        AS compras_google,
                        ROUND(SUM(compras), 4)                                            AS compras_total
                    FROM `guara-db.ingestao.ing_ads_diario`
                    GROUP BY 1, 2
                ),
                shopify_dedup AS (
                    -- Deduplica: mantém apenas a linha mais recente por (cliente, data)
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
                        DATE_TRUNC(data, MONTH)                                           AS mes,
                        ROUND(SUM(receita), 2)                                            AS receita_shopify,
                        SUM(pedidos)                                                      AS pedidos,
                        SUM(novos_clientes)                                               AS novos_clientes,
                        SUM(recompradores)                                                AS recompradores,
                        ROUND(AVG(ticket_medio), 2)                                       AS ticket_medio,
                        ROUND(SUM(total_desconto), 2)                                     AS total_desconto,
                        SUM(pedidos_gt300)                                            AS pedidos_gt300
                    FROM shopify_dedup
                    GROUP BY 1, 2
                ),
                base AS (
                    SELECT
                        a.cliente, a.mes,
                        a.investimento_meta, a.investimento_google, a.investimento_total,
                        a.compras_meta, a.compras_google, a.compras_total,
                        s.receita_shopify, s.pedidos, s.novos_clientes, s.recompradores,
                        s.ticket_medio, s.total_desconto, s.pedidos_gt300,
                        ROUND(s.receita_shopify / NULLIF(a.investimento_total, 0), 2)    AS roas_real,
                        ROUND(a.investimento_total / NULLIF(a.compras_total, 0), 2)      AS cpa,
                        ROUND(s.recompradores / NULLIF(s.pedidos, 0) * 100, 1)          AS pct_retencao,
                        ROUND(s.novos_clientes / NULLIF(s.pedidos, 0) * 100, 1)         AS pct_novos,
                        ROUND(s.pedidos_gt300 / NULLIF(s.pedidos, 0) * 100, 1)      AS pct_premium
                    FROM ads_mes a
                    LEFT JOIN shopify_mes s USING (cliente, mes)
                )
                SELECT
                    *,
                    LAG(investimento_total) OVER w  AS invest_mes_ant,
                    LAG(receita_shopify)    OVER w  AS receita_mes_ant,
                    LAG(roas_real)          OVER w  AS roas_mes_ant,
                    LAG(compras_total)      OVER w  AS compras_mes_ant,
                    LAG(cpa)               OVER w  AS cpa_mes_ant,
                    ROUND((investimento_total - LAG(investimento_total) OVER w)
                          / NULLIF(LAG(investimento_total) OVER w, 0) * 100, 1) AS var_invest_pct,
                    ROUND((receita_shopify - LAG(receita_shopify) OVER w)
                          / NULLIF(LAG(receita_shopify) OVER w, 0) * 100, 1)   AS var_receita_pct,
                    ROUND((roas_real - LAG(roas_real) OVER w)
                          / NULLIF(LAG(roas_real) OVER w, 0) * 100, 1)         AS var_roas_pct,
                    LAG(receita_shopify, 12) OVER w   AS receita_yoy,
                    LAG(roas_real, 12)       OVER w   AS roas_yoy,
                    LAG(investimento_total, 12) OVER w AS invest_yoy,
                    ROUND((receita_shopify - LAG(receita_shopify, 12) OVER w)
                          / NULLIF(LAG(receita_shopify, 12) OVER w, 0) * 100, 1) AS var_receita_yoy_pct,
                    ROUND((roas_real - LAG(roas_real, 12) OVER w)
                          / NULLIF(LAG(roas_real, 12) OVER w, 0) * 100, 1)     AS var_roas_yoy_pct
                FROM base
                WINDOW w AS (PARTITION BY cliente ORDER BY mes)
                ORDER BY cliente, mes DESC;
