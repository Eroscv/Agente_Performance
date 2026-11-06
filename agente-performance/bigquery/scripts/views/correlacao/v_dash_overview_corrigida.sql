-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.correlacao.v_dash_overview_corrigida` AS
SELECT
  cliente_id,
  CASE cliente_id
    WHEN 'source_of' THEN 'Source OF' WHEN 'pod_kombucha' THEN 'POD Kombucha' WHEN 'botica' THEN 'Botica'
    ELSE cliente_id END AS cliente_nome,
  canal AS plataforma,
  data,
  ROUND(investimento, 2) AS investimento,
  ROUND(CASE WHEN canal = 'meta' THEN investimento * 1.1215 ELSE investimento END, 2) AS investimento_ajustado,
  pedidos,
  receita,
  cliques_link AS cliques,
  roas_real
FROM `guara-db.correlacao.fato_performance_diaria`;
