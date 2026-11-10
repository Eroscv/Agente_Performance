-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_performance_14_30d` AS
WITH base AS (
    SELECT
        cliente,
        plataforma,
        data,
        investimento,
        compras,
        receita,
        impressoes,
        cliques_todos AS cliques_link,
        CASE WHEN plataforma = 'meta'
             THEN investimento * 1.1215
             ELSE investimento END AS investimento_ajustado
    FROM `guara-db.ingestao.ing_ads_diario`
    WHERE data >= DATE_SUB(CURRENT_DATE(), INTERVAL 31 DAY)
),
agg AS (
    SELECT
        cliente,
        plataforma,
        -- Janela 14 dias
        ROUND(SUM(CASE WHEN data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
                       THEN investimento_ajustado END), 2)          AS invest_14d,
        ROUND(SUM(CASE WHEN data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
                       THEN compras END), 2)                        AS compras_14d,
        ROUND(SUM(CASE WHEN data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
                       THEN receita END), 2)                        AS receita_14d,
        -- Janela 30 dias
        ROUND(SUM(investimento_ajustado), 2)                        AS invest_30d,
        ROUND(SUM(compras), 2)                                      AS compras_30d,
        ROUND(SUM(receita), 2)                                      AS receita_30d,
        -- CPAs e ROAS
        ROUND(SUM(CASE WHEN data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
                       THEN investimento_ajustado END)
              / NULLIF(SUM(CASE WHEN data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
                                THEN compras END), 0), 2)           AS cpa_14d,
        ROUND(SUM(CASE WHEN data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
                       THEN receita END)
              / NULLIF(SUM(CASE WHEN data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
                                THEN investimento_ajustado END), 0), 2) AS roas_14d,
        ROUND(SUM(investimento_ajustado)
              / NULLIF(SUM(compras), 0), 2)                         AS cpa_30d,
        ROUND(SUM(receita)
              / NULLIF(SUM(investimento_ajustado), 0), 2)           AS roas_30d,
        CURRENT_DATE() AS referencia
    FROM base
    GROUP BY cliente, plataforma
)
SELECT * FROM agg
ORDER BY cliente, plataforma;
