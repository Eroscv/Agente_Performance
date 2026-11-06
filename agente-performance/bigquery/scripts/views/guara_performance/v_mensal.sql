-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_mensal` AS
SELECT
    cliente,
    plataforma,
    DATE_TRUNC(data, MONTH)                                       AS mes,
    ROUND(SUM(investimento), 2)                                   AS investimento,
    ROUND(SUM(CASE WHEN plataforma='meta'
                   THEN investimento * 1.1215
                   ELSE investimento END), 2)                     AS investimento_ajustado,
    ROUND(SUM(receita), 2)                                        AS receita,
    ROUND(SUM(compras), 4)                                        AS compras,
    ROUND(SUM(receita) / NULLIF(SUM(investimento), 0), 2)         AS roas,
    ROUND(SUM(receita) / NULLIF(
        SUM(CASE WHEN plataforma='meta'
                 THEN investimento * 1.1215
                 ELSE investimento END), 0), 2)                   AS roas_real,
    ROUND(SUM(investimento) / NULLIF(SUM(compras), 0), 2)         AS cpa,
    ROUND(SUM(impressoes), 0)                                     AS impressoes,
    ROUND(SUM(cliques_link), 0)                                   AS cliques
FROM `guara-db.ingestao.ing_ads_diario`
GROUP BY 1, 2, 3;
