-- Consumo do Agente de Performance: Meta por anúncio (criativos), mesma forma da view local
-- meta_anuncios (db/duck.py). Fonte: camada tratada, nível anúncio.
CREATE OR REPLACE VIEW `guara-db.correlacao.v_agente_anuncios` AS
SELECT
  cliente AS cliente_key,
  'meta_ads' AS plataforma,
  data,
  campanha,
  campaign_id,
  adset_name,
  adset_id,
  ad_name,
  ad_id,
  objetivo,
  COALESCE(impressoes, 0) AS impressoes,
  COALESCE(alcance, 0) AS alcance,
  COALESCE(cliques_link, 0) AS cliques_link,
  CAST(COALESCE(investimento, 0) AS FLOAT64) AS investimento,
  COALESCE(compras, 0) AS compras,
  CAST(COALESCE(receita, 0) AS FLOAT64) AS valor_compras,
  frequencia
FROM `guara-db.ingestao.ing_ads_diario`
WHERE plataforma = 'meta' AND nivel = 'anuncio';
