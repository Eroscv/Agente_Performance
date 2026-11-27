-- Consumo do Agente de Performance: mesma forma da view local v_performance (db/duck.py),
-- para as consultas do agente rodarem no BigQuery sem tradução de colunas.
-- Fonte: camada tratada, só as linhas que entram no total (Meta por anúncio, Google por
-- campanha), a mesma regra do gold. Uma linha por cliente, plataforma, dia e anúncio/campanha.
--   plataforma  → 'meta_ads' | 'google_ads' (como o agente filtra)
--   roas / cpa  → atribuição da plataforma; no Meta o investimento leva o imposto (×1,1383),
--                 menos na Botica, como na view local
--   investimento → sempre bruto, sem imposto
--   ctr em % (cliques ÷ impressões × 100); cpc_medio só no Google
CREATE OR REPLACE VIEW `guara-db.correlacao.v_agente_performance` AS
SELECT
  cliente AS cliente_key,
  CASE cliente
    WHEN 'source_of' THEN 'Source OF'
    WHEN 'pod_kombucha' THEN 'POD Kombucha'
    WHEN 'botica' THEN 'Botica Ouro Preto'
    ELSE cliente
  END AS cliente_nome,
  CONCAT(plataforma, '_ads') AS plataforma,
  data,
  campanha,
  campaign_id,
  objetivo,
  status,
  COALESCE(impressoes, 0) AS impressoes,
  COALESCE(cliques_link, 0) AS cliques,
  IF(plataforma = 'meta', COALESCE(alcance, 0), NULL) AS alcance,
  CAST(COALESCE(investimento, 0) AS FLOAT64) AS investimento,
  COALESCE(compras, 0) AS conversoes,
  CAST(COALESCE(receita, 0) AS FLOAT64) AS receita,
  ROUND(SAFE_DIVIDE(CAST(receita AS FLOAT64),
        NULLIF(CAST(investimento AS FLOAT64) * IF(plataforma = 'meta' AND cliente != 'botica', 1.1383, 1), 0)), 4) AS roas,
  ROUND(SAFE_DIVIDE(CAST(investimento AS FLOAT64) * IF(plataforma = 'meta' AND cliente != 'botica', 1.1383, 1),
        NULLIF(compras, 0)), 2) AS cpa,
  ROUND(SAFE_DIVIDE(cliques_link, NULLIF(impressoes, 0)) * 100, 4) AS ctr,
  IF(plataforma = 'google', ROUND(SAFE_DIVIDE(CAST(investimento AS FLOAT64), NULLIF(cliques_link, 0)), 2), NULL) AS cpc_medio,
  IF(plataforma = 'meta', COALESCE(video_3s, 0), NULL) AS video_3s,
  IF(plataforma = 'meta', COALESCE(thruplays, 0), NULL) AS thruplays
FROM `guara-db.ingestao.ing_ads_diario`
WHERE entra_no_total;
