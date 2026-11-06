-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_dash_overview` AS
SELECT
    cliente,
    CASE cliente
        WHEN 'source_of'    THEN 'Source OF'
        WHEN 'pod_kombucha' THEN 'POD Kombucha'
        WHEN 'botica'       THEN 'Botica'
        ELSE cliente
    END                                                     AS cliente_nome,
    plataforma,
    data,
    ROUND(SUM(investimento), 2)                                         AS investimento,
    ROUND(SUM(CASE WHEN plataforma = 'meta' THEN investimento * 1.1215 ELSE investimento END), 2) AS investimento_ajustado,
    ROUND(SUM(compras), 0)                                              AS compras,
    ROUND(SUM(receita), 2)                                              AS receita_plataforma,
    ROUND(SUM(impressoes), 0)                                           AS impressoes,
    ROUND(SUM(alcance), 0)                                              AS alcance,
    ROUND(SUM(cliques_link), 0)                                         AS cliques,
    ROUND(SUM(adicoes_carrinho), 0)                                     AS adicoes_carrinho,
    ROUND(SUM(checkout_iniciado), 0)                                    AS checkout_iniciado,
    ROUND(SUM(video_3s), 0)                                             AS video_3s,
    ROUND(SUM(thruplays), 0)                                            AS thruplays,
    ROUND(AVG(frequencia), 2)                                           AS frequencia
FROM (
    SELECT * EXCEPT(rn)
    FROM (
        SELECT * EXCEPT(nivel, dq_cliques_todos_ausente),
            ROW_NUMBER() OVER (
                PARTITION BY cliente, plataforma, data, campaign_id, adset_id, ad_id
                ORDER BY criado_em DESC
            ) AS rn,
            CASE WHEN ad_id IS NOT NULL THEN 'anuncio'
                 WHEN ad_id IS NULL AND adset_id IS NOT NULL THEN 'adset'
                 ELSE 'campanha' END AS nivel
        FROM `guara-db.ingestao.ing_ads_diario`
    )
    WHERE rn = 1 AND entra_no_total
)
GROUP BY 1, 2, 3, 4;
