# Views do BigQuery

Uma pasta por dataset, um arquivo por view, com a definição que está em produção.
Mudou uma view? Edite o arquivo, abra revisão e só depois aplique com `CREATE OR REPLACE VIEW`.
Os scripts `scripts/create_bq_views_dashboard*.py` e `create_bq_view_todos_dados.py` estão
defasados em relação a estas definições e não devem ser rodados.
