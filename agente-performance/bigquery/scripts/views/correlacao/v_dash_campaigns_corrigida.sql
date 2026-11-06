-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.correlacao.v_dash_campaigns_corrigida` AS
SELECT
  COALESCE(a.cliente_id, ad.cliente) AS cliente_id,
  CASE COALESCE(a.cliente_id, ad.cliente)
    WHEN 'source_of' THEN 'Source OF' WHEN 'pod_kombucha' THEN 'POD Kombucha' WHEN 'botica' THEN 'Botica'
    ELSE COALESCE(a.cliente_id, ad.cliente) END AS cliente_nome,
  ad.plataforma,
  ad.data,
  ad.campanha,
  ad.campaign_id,
  CASE WHEN ad.plataforma = 'google' OR ad.objetivo IS NULL OR ad.objetivo = '' THEN 'Google' ELSE ad.objetivo END AS campaign_type,
  ad.status,
  ROUND(SUM(ad.investimento), 2) AS investimento,
  ROUND(SUM(CASE WHEN ad.plataforma = 'meta' THEN ad.investimento * 1.1215 ELSE ad.investimento END), 2) AS amount_spent,
  ROUND(SUM(ad.compras), 0) AS compras,
  ROUND(SUM(ad.receita), 2) AS receita_plataforma,
  ROUND(SUM(ad.cliques_link), 0) AS cliques
FROM `guara-db.ingestao.ing_ads_diario` ad
LEFT JOIN `guara-db.correlacao.dim_cliente_alias` a ON a.alias = ad.cliente
WHERE ad.nivel = 'anuncio'
GROUP BY 1,2,3,4,5,6,7,8;
