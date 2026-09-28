# Alterações — 2026-08-19

**Sprint:** DuckDB + Shopify POD + Relatório Multi-plataforma + Guardrails

---

## Commits desta data

| # | Tipo | Descrição |
|---|---|---|
| 1 | feat | DuckDB — db/duck.py + 24 testes de paridade vs SQLite (24/24 passando) |
| 2 | fix | Corrige 3 regressões DuckDB — .description, LIKE case-insensitive, test is→isinstance |
| 3 | refactor | Migra 26 arquivos de db.connection → db.connections |
| 4 | feat | DuckDB Etapa 2 — etl_warehouse.py + carga histórica completa |
| 5 | feat | DuckDB Etapa 3 — lê Parquet via views dw_* |
| 6 | docs | SCHEMA.md — modelo star schema para DW local DuckDB+Parquet |
| 7 | feat | Shopify integração POD Kombucha |
| 8 | feat | Intent comparar_plataformas — Meta Ads vs Google Ads lado a lado |
| 9 | feat | Relatório diário multi-plataforma consolidado — uma thread + um link Notion por cliente |
| 10 | feat | Página Notion consolidada por cliente — Google Ads + Meta Ads + Shopify em uma página |
| 11 | feat | Guardrails de qualidade de dados (volume mínimo, queda brusca, janela de atribuição) |
| 12 | feat | DuckDB window functions no relatório diário |
| 13 | fix | Sync Meta Ads grava no DB correto + reset federado pós-sync |
| 14 | fix | Sync Google Ads grava no banco federado correto (source_of/google_ads.db) |
| 15 | fix | Corrige default enganoso em plataforma_meta (None em vez de 'google_ads') |
| 16 | fix | Source OF Google Ads retorna dados de ambas as plataformas quando não especificado |
| 17 | fix | Shopify só aparece na thread Meta Ads — não duplicar no Google Ads |
| 18 | fix | Cada plataforma em mensagem separada na thread + link Notion como última mensagem |
| 19 | fix | ROAS 0.00x exibido corretamente — c.get('roas') is not None |
| 20 | fix | Período do alerta ROAS usa data_maxima do banco por cliente |
| 21 | fix | shopify_receita_diaria — CAST(data AS VARCHAR) para consistência de tipo |
| 22 | fix | verificar_alertas — SUM(receita) → SUM(valor_compras) na view campanhas_unificada |
| 23 | fix | Detecção de plataformas no cron usa v_performance em vez de tabelas brutas |
| 24 | fix | Emojis como texto (":bar_chart:") → unicode + link "Abrir no Notion" duplicado removido |
| 25 | fix | source_of_shopify_diario → shopify_receita_diaria + mensagem sem_dados mais clara |
| 26 | refactor | Views federadas — auto-reconexão, inspect_federated_views(), db/views.sql |
| 27 | docs | QA_REPORT.md — 39 casos manuais + 201 testes automatizados |

---

## Arquivos alterados (principais)

- `db/duck.py` — novo módulo DuckDB
- `db/connections.py` — migrado para suporte DuckDB + SQLite
- `db/views.sql` — views federadas com auto-reconexão
- `db/etl_warehouse.py` — ETL histórico para Parquet
- `agent/engine.py` — intent comparar_plataformas + guardrails
- `notion/relatorio.py` — página consolidada multi-plataforma
- `slack/poster.py` — thread por plataforma + link Notion final
- `shopify/client.py` — integração POD Kombucha
- `queries/alertas.py` — fix ROAS + data_maxima por cliente
- 26 outros arquivos migrados de db.connection → db.connections

---

## Impacto

| Área | Antes | Depois |
|---|---|---|
| Banco de dados | Apenas SQLite | SQLite + DuckDB + Parquet (star schema) |
| POD Kombucha Shopify | Sem integração | Dados Shopify sincronizados e exibidos no relatório |
| Relatório Slack | Uma mensagem por plataforma separada | Uma thread consolidada por cliente, uma página Notion |
| Qualidade de dados | Sem validação | Guardrails: volume mínimo, queda brusca, janela de atribuição |
| Alertas ROAS | Exibia 0.00x incorretamente / período errado | Exibe corretamente + usa data real do banco |
| Comparativo plataformas | Não existia | Pergunta "compare Meta vs Google" respondida lado a lado |
| Sync banco federado | Gravava no banco errado em alguns casos | Cada plataforma grava no banco correto |
