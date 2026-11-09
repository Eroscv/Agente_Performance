-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_dash_sources` AS
SELECT
    cliente,
    
    CASE cliente
        WHEN 'source_of'    THEN 'Source OF'
        WHEN 'pod_kombucha' THEN 'POD Kombucha'
        WHEN 'botica'       THEN 'Botica'
        ELSE cliente
    END                                                     AS cliente_nome,
    plataforma                                                          AS source,
    data,

    ROUND(SUM(investimento), 2)                                         AS investimento,
    ROUND(SUM(CASE WHEN plataforma = 'meta' THEN investimento * 1.1215 ELSE investimento END), 2)                                            AS amount_spent,
    ROUND(SUM(compras), 0)                                              AS compras,
    ROUND(SUM(receita), 2)                                              AS receita_plataforma,
    ROUND(SUM(impressoes), 0)                                           AS impressoes,
    ROUND(SUM(cliques_todos), 0)                                         AS cliques

FROM (
    SELECT * EXCEPT(rn, nivel, dq_cliques_todos_ausente)
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY cliente, plataforma, data, campaign_id, adset_id, ad_id
                ORDER BY criado_em DESC
            ) AS rn
        FROM `guara-db.ingestao.ing_ads_diario`
    )
    WHERE rn = 1
)
GROUP BY 1, 2, 3, 4;
