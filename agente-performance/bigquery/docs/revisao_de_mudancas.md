# Revisão de mudanças no BigQuery

Em 05/11/2026, 19 views do `guara_performance` foram recriadas em produção entre 17:29 e 17:38,
direto pela service account, sem revisor. O histórico do BigQuery (`INFORMATION_SCHEMA.JOBS`)
mostra 43 `CREATE VIEW`, 14 `CREATE PROCEDURE` e 6 `CREATE FUNCTION` pela service account
`agente-guara-bq` entre 05/11 e 06/11. Esta regra existe para que isso não se repita.

## Regra

Toda mudança em view, procedure, função ou DDL de produção:

1. **Vai por branch e pull request.** O SQL fica em `bigquery/scripts/` (views em
   `bigquery/scripts/views/<dataset>/<view>.sql`), nunca só no console ou num script solto.
2. **Tem revisor** diferente de quem escreveu. O revisor das mudanças de produção do BigQuery é o **Miguel**, que aprova o pull request (modelo em
   `.github/pull_request_template.md`: antes e depois, verificações de qualidade).
3. **Só é aplicada pelo `scripts/apply_bq_sql.py`**, depois do merge. O script recusa arquivo
   não commitado e grava em `correlacao.log_alteracoes_bq` quem aplicou, quando, o commit, o
   hash do arquivo e o revisor. Sem `--revisor "Miguel"` o script recusa (a não ser com `--forcar`,
   que fica registrado como forçado).
4. Depois de aplicar, rodar `python3 scripts/run_quality_checks.py`.

## O que não está coberto

- O `apply_bq_sql.py` registra e exige commit, mas não impede quem tem a credencial da service
  account de rodar SQL por fora. Impedir de fato exigiria separar permissões (quem lê, quem altera).
- Os scripts de carga (`load_silver`, `load_gold`, syncs) rodam sob a mesma service account; só
  as mudanças de definição passam por esta regra.
