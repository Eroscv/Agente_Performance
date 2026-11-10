-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_dashboard_diario_consolidado` AS
SELECT
    cliente,
    plataforma,
    data,

    ROUND(SUM(investimento), 2)                                         AS investimento,
    ROUND(SUM(investimento_ajustado), 2)                                AS investimento_ajustado,
    ROUND(SUM(receita), 2)                                              AS receita,
    ROUND(SUM(compras), 4)                                              AS compras,

    ROUND(SUM(receita) / NULLIF(SUM(investimento_ajustado), 0), 4)      AS roas_real,
    ROUND(SUM(investimento_ajustado) / NULLIF(SUM(compras), 0), 2)      AS cpa_real,

    ROUND(SUM(impressoes), 0)                                           AS impressoes,
    ROUND(SUM(alcance), 0)                                              AS alcance,
    ROUND(SUM(cliques_todos), 0)                                         AS cliques_link,
    ROUND(SUM(cliques_todos) / NULLIF(SUM(impressoes), 0) * 100, 4)      AS ctr,
    ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2)     AS cpm,
    ROUND(AVG(frequencia), 2)                                           AS frequencia,

    ROUND(SUM(video_3s), 0)                                             AS video_3s,
    ROUND(SUM(thruplays), 0)                                            AS thruplays,
    ROUND(SUM(adicoes_carrinho), 0)                                     AS adicoes_carrinho,
    ROUND(SUM(checkout_iniciado), 0)                                    AS checkout_iniciado

FROM `guara-db.guara_performance.v_dashboard_diario`
GROUP BY 1, 2, 3
ORDER BY cliente, plataforma, data;
