-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_top_campanhas_mes` AS
SELECT
    cliente,
    plataforma,
    campanha,
    ROUND(SUM(investimento), 2)                                   AS investimento,
    ROUND(SUM(CASE WHEN plataforma='meta'
                   THEN investimento * 1.1215
                   ELSE investimento END), 2)                     AS investimento_ajustado,
    ROUND(SUM(receita), 2)                                        AS receita,
    ROUND(SUM(compras), 4)                                        AS compras,
    ROUND(SUM(receita) / NULLIF(
        SUM(CASE WHEN plataforma='meta'
                 THEN investimento * 1.1215
                 ELSE investimento END), 0), 2)                   AS roas_real,
    ROUND(SUM(impressoes), 0)                                     AS impressoes,
    ROUND(SUM(cliques_link), 0)                                   AS cliques,
    ROUND(SUM(cliques_link) / NULLIF(SUM(impressoes), 0) * 100, 2) AS ctr
FROM `guara-db.ingestao.ing_ads_diario`
WHERE data >= DATE_TRUNC(CURRENT_DATE(), MONTH)
GROUP BY 1, 2, 3
ORDER BY investimento_ajustado DESC;
