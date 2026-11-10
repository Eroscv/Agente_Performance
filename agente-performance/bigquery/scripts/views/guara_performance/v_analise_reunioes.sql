-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_analise_reunioes` AS
SELECT * EXCEPT(rn)
        FROM (
          SELECT *,
            ROW_NUMBER() OVER (
              PARTITION BY fonte_id
              ORDER BY COALESCE(atualizado_em, analisado_em) DESC
            ) AS rn
          FROM `guara-db.guara_performance.analise_reunioes`
        )
        WHERE rn = 1;
