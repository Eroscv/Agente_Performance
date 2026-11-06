-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_ads_diario` AS
SELECT * EXCEPT(rn, nivel, dq_cliques_todos_ausente)
FROM (
  SELECT *,
    ROW_NUMBER() OVER (
      PARTITION BY cliente, plataforma, data, campaign_id, adset_id, ad_id
      ORDER BY criado_em DESC
    ) AS rn
  FROM `guara-db.ingestao.ing_ads_diario`
)
WHERE rn = 1;
