# Revisão de mudanças no BigQuery

Em 05/11/2026, 19 views do `guara_performance` foram recriadas em produção entre 17:29 e 17:38,
direto pela service account, sem revisor. O histórico do BigQuery (`INFORMATION_SCHEMA.JOBS`)
mostra 43 `CREATE VIEW`, 14 `CREATE PROCEDURE` e 6 `CREATE FUNCTION` pela service account
`agente-guara-bq` entre 05/11 e 06/11. Esta regra existe para que isso não se repita.

## Regra

Toda mudança em view, procedure, função ou DDL de produção:

1. **O SQL fica no repositório e commitado** em `bigquery/scripts/` (views em
   `bigquery/scripts/views/<dataset>/<view>.sql`), nunca só no console ou num script solto.
2. **O Miguel revisa direto no BigQuery**, antes de a mudança ir para produção. Quem altera pede
   a revisão e informa o arquivo; o Miguel confere no BigQuery:
   - a definição atual em produção (`INFORMATION_SCHEMA.VIEWS` ou `ROUTINES`) contra o arquivo;
   - o antes e depois dos números afetados (contagem de linhas e totais);
   - o resultado de `python3 scripts/run_quality_checks.py` (0 falhas).
3. **Só depois do OK do Miguel** a mudança é aplicada com
   `python3 scripts/apply_bq_sql.py --revisor "Miguel" <arquivo>`. O script recusa arquivo não
   commitado ou sem `--revisor` (a não ser com `--forcar`, que fica registrado como forçado) e grava
   em `correlacao.log_alteracoes_bq` quem aplicou, quando, o commit, o hash do arquivo e o revisor.
4. Depois de aplicar, rodar `python3 scripts/run_quality_checks.py`.

## O que não está coberto

- O OK do Miguel é dado fora do script (ele não consegue verificar sozinho que a revisão aconteceu): o
  `--revisor "Miguel"` registra quem foi informado como revisor, e o Miguel pode conferir em
  `log_alteracoes_bq` o que foi aplicado em seu nome.
- O `apply_bq_sql.py` registra e exige commit, mas não impede quem tem a credencial da service
  account de rodar SQL por fora. Impedir de fato exigiria separar permissões (quem lê, quem altera).
- Os scripts de carga (`load_silver`, `load_gold`, syncs) rodam sob a mesma service account; só
  as mudanças de definição passam por esta regra.
