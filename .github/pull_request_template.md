## O que muda
<!-- Resumo em 1 ou 2 frases -->

## Mexe em produção do BigQuery?
- [ ] Não
- [ ] Sim: view, procedure, função ou DDL em `bigquery/scripts/`

## Se mexe em produção do BigQuery
- [ ] O SQL alterado está em `bigquery/scripts/` (views em `bigquery/scripts/views/`)
- [ ] Comparei antes e depois (contagem de linhas e totais) e colei o resultado abaixo
- [ ] `python3 scripts/run_quality_checks.py` passou (0 falhas)
- [ ] Miguel revisou direto no BigQuery e deu o OK antes de aplicar com `scripts/apply_bq_sql.py --revisor "Miguel"`

## Antes e depois
<!-- números -->
