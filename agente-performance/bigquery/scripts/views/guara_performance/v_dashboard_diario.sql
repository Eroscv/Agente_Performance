-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_dashboard_diario` AS
WITH dedup AS (
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
SELECT
    cliente,
    plataforma,
    data,
    campanha,
    campaign_id,
    adset_name,
    adset_id,
    ad_name,
    ad_id,
    objetivo,
    status,
    account_id,

    -- investimento bruto (sem ajuste)
    ROUND(investimento, 2)                                              AS investimento,

    -- investimento ajustado fiscal (Meta ×1.1215, Google inalterado)
    ROUND(
        CASE WHEN plataforma = 'meta'
             THEN investimento * 1.1215
             ELSE investimento
        END, 2
    )                                                                   AS investimento_ajustado,

    -- conversões / receita atribuída pela plataforma
    compras,
    ROUND(receita, 2)                                                   AS receita,

    -- ROAS real (sobre investimento ajustado)
    ROUND(
        receita / NULLIF(
            CASE WHEN plataforma = 'meta'
                 THEN investimento * 1.1215
                 ELSE investimento
            END, 0), 4
    )                                                                   AS roas_real,

    -- CPA real
    ROUND(
        CASE WHEN plataforma = 'meta'
             THEN investimento * 1.1215
             ELSE investimento
        END / NULLIF(compras, 0), 2
    )                                                                   AS cpa_real,

    -- alcance / entrega
    impressoes,
    alcance,
    cliques_link,
    cliques_todos,
    ROUND(cliques_todos / NULLIF(impressoes, 0) * 100, 4)                AS ctr,
    ROUND(investimento / NULLIF(impressoes, 0) * 1000, 2)               AS cpm_real,
    frequencia,

    -- vídeo / funil
    video_3s,
    video_50pct,
    thruplays,
    adicoes_carrinho,
    checkout_iniciado,
    visualizacoes_landing,

    -- rankings
    qualidade_ranking,
    engagement_ranking,
    conversion_ranking,

    criado_em
FROM dedup;
