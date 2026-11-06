-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_tickets_alerta` AS
SELECT * EXCEPT(rn)
        FROM (
          SELECT *,
            ROW_NUMBER() OVER (
              PARTITION BY ticket_id
              ORDER BY atualizado_em DESC
            ) AS rn
          FROM `guara-db.ingestao.ing_tickets`
        )
        WHERE rn = 1;
