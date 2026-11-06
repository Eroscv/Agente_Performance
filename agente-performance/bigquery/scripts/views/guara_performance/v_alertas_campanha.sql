-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_alertas_campanha` AS
WITH base AS (
    SELECT
        cliente, plataforma, campanha, data,
        investimento, receita, compras, roas,
        AVG(roas) OVER (
            PARTITION BY cliente, plataforma, campanha
            ORDER BY data, ad_id, adset_id
            ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING
        ) AS roas_media_7d
    FROM `guara-db.ingestao.ing_ads_diario`
    WHERE investimento > 0
)
SELECT
    *,
    CASE
        WHEN roas_media_7d IS NULL        THEN 'novo'
        WHEN roas < roas_media_7d * 0.5   THEN 'critico'
        WHEN roas < roas_media_7d * 0.75  THEN 'alerta'
        WHEN roas > roas_media_7d * 1.25  THEN 'destaque'
        ELSE                                   'normal'
    END AS status_alerta
FROM base;
