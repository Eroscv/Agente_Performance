# `bigquery/`

Documentação e scripts SQL versionados da arquitetura medallion do
`guara-db`, organizados no mesmo espírito do
[DataWithBaraa/sql-data-warehouse-project](https://github.com/DataWithBaraa/sql-data-warehouse-project)
(adaptado de SQL Server para BigQuery).

- [`docs/data_architecture.md`](docs/data_architecture.md) — diagrama e
  explicação de bronze → silver → gold.
- [`docs/naming_conventions.md`](docs/naming_conventions.md) — convenções
  de nome por camada.
- [`docs/data_catalog_gold.md`](docs/data_catalog_gold.md) — dicionário de
  dados das tabelas gold (`correlacao`).
- `scripts/silver/proc_load_silver.sql` / `scripts/gold/proc_load_gold.sql`
  — definição das stored procedures `ingestao.load_silver()` e
  `correlacao.load_gold()`. São a fonte da verdade da lógica de
  transformação; `scripts/rebuild_bq_layers.py` (na raiz do repo) só as
  chama.
- `tests/quality_checks_silver.sql` / `tests/quality_checks_gold.sql` —
  checagens que devem sempre retornar zero linhas. Rode depois de qualquer
  `CALL load_silver()` / `CALL load_gold()` manual.

## Como alterar uma procedure

1. Edite o `.sql` correspondente.
2. Rode o `CREATE OR REPLACE PROCEDURE` inteiro no BigQuery (console, `bq`
   CLI, ou a ferramenta MCP do BigQuery) — é idempotente.
3. Rode os quality checks pra confirmar que nada quebrou.

Nenhum script Python precisa mudar: `scripts/rebuild_bq_layers.py` sempre
chama a versão mais recente da procedure.
