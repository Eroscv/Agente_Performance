-- Situação da carga por cliente, fonte e dia (últimos 60 dias, dia de Brasília).
--   ok            → pelo menos uma sincronização com sucesso no dia (zero vendas também é ok)
--   falhou        → sincronizou, mas todas as tentativas do dia deram erro
--   sem_execucao  → nenhuma sincronização registrada no dia (máquina desligada, cron parado)
-- Os pares cliente e fonte esperados são os que tiveram alguma sincronização nos últimos 30 dias.
CREATE OR REPLACE VIEW `guara-db.correlacao.v_status_cargas` AS
WITH registros AS (
  SELECT cliente, fonte, DATE(executado_em, 'America/Sao_Paulo') AS dia, sucesso, erro, executado_em
  FROM `guara-db.ingestao.log_cargas_fontes`
  WHERE DATE(executado_em, 'America/Sao_Paulo') >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 60 DAY)
),
pares AS (
  SELECT DISTINCT cliente, fonte FROM registros
  WHERE dia >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 30 DAY)
),
inicio AS (
  SELECT MIN(dia) AS primeiro_dia FROM registros
),
dias AS (
  SELECT dia
  FROM inicio, UNNEST(GENERATE_DATE_ARRAY(primeiro_dia, CURRENT_DATE('America/Sao_Paulo'))) AS dia
),
por_dia AS (
  SELECT cliente, fonte, dia,
         COUNT(*) AS execucoes,
         COUNTIF(sucesso) AS sucessos,
         ARRAY_AGG(IF(sucesso, NULL, erro) IGNORE NULLS ORDER BY executado_em DESC LIMIT 1)[SAFE_OFFSET(0)] AS ultimo_erro,
         MAX(executado_em) AS ultima_execucao
  FROM registros
  GROUP BY cliente, fonte, dia
)
SELECT p.cliente, p.fonte, d.dia,
       COALESCE(x.execucoes, 0) AS execucoes,
       COALESCE(x.sucessos, 0) AS sucessos,
       CASE
         WHEN x.sucessos > 0 THEN 'ok'
         WHEN x.execucoes > 0 THEN 'falhou'
         ELSE 'sem_execucao'
       END AS situacao,
       x.ultimo_erro,
       x.ultima_execucao
FROM pares p
CROSS JOIN dias d
LEFT JOIN por_dia x ON x.cliente = p.cliente AND x.fonte = p.fonte AND x.dia = d.dia;
