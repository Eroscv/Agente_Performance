-- Definição da view em produção (exportada em 06/11/2026). Fonte da verdade no repositório:
-- alterar aqui, revisar e só então aplicar com CREATE OR REPLACE VIEW.
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_benchmark_mensal` AS
SELECT
    cliente,
    plataforma,
    mes,
    investimento_ajustado,
    receita,
    compras,
    roas_real,
    cpa,
    LAG(roas_real)            OVER w                              AS roas_real_mes_ant,
    LAG(investimento_ajustado) OVER w                             AS invest_mes_ant,
    LAG(receita)              OVER w                              AS receita_mes_ant,
    ROUND((roas_real - LAG(roas_real) OVER w)
          / NULLIF(LAG(roas_real) OVER w, 0) * 100, 1)           AS variacao_roas_pct,
    ROUND((receita - LAG(receita) OVER w)
          / NULLIF(LAG(receita) OVER w, 0) * 100, 1)             AS variacao_receita_pct
FROM `guara-db.guara_performance.v_mensal`
WINDOW w AS (PARTITION BY cliente, plataforma ORDER BY mes);
