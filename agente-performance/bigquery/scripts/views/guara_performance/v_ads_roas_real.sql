-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_ads_roas_real` AS
SELECT
    *,
    ROUND(
        CASE WHEN plataforma = 'meta' THEN investimento * 1.1215
             ELSE investimento
        END, 2
    ) AS investimento_ajustado,
    ROUND(
        receita / NULLIF(
            CASE WHEN plataforma = 'meta' THEN investimento * 1.1215
                 ELSE investimento
            END, 0), 2
    ) AS roas_real,
    ROUND(
        CASE WHEN plataforma = 'meta' THEN investimento * 1.1215
             ELSE investimento
        END / NULLIF(compras, 0), 2
    ) AS cpa_real
FROM `guara-db.ingestao.ing_ads_diario`;
