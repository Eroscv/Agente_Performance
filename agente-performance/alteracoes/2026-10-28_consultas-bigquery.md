# Alteração: BigQuery como fonte primária para todas as consultas do agente

**Data:** 2026-10-28
**Commit:** b93e2b7
**Autor:** Cesar (Agente)

## Arquivos alterados

| Arquivo | Tipo |
|---|---|
| `db/bq_translator.py` | Criado |
| `db/bq_reader.py` | Atualizado |
| `db/connections.py` | Atualizado |

## Arquitetura

**Antes:** `query_fed` → DuckDB → SQLite local

**Depois:** `query_fed` → BigQuery (via `bq_translator`) → fallback DuckDB se BQ cair

O `bq_translator.py` funciona como uma camada de compatibilidade transparente — todos os módulos existentes continuam enviando SQL para `campanhas_unificada`/`v_performance`/`shopify_receita_diaria`, e o tradutor converte automaticamente para o equivalente BigQuery.

## Cobertura (11/11 ✅)

| Módulo | Status |
|---|---|
| `resumo_periodo` | ✅ |
| `top_campanhas_roas` | ✅ |
| `top_campanhas_compras` | ✅ |
| `top_campanhas_receita` | ✅ |
| `alertas_roas_baixo` | ✅ |
| `campanhas_sem_conversao` | ✅ |
| `campanhas_inativas` | ✅ |
| `evolucao_diaria` | ✅ |
| `variacao_diaria_roas` | ✅ |
| `receita_shopify_periodo` | ✅ |
| `metricas_shopify_diario` | ✅ |

## Transformações do tradutor

| Problema | Solução |
|---|---|
| `campanhas_unificada` → tabela BQ | `ads_diario` |
| `shopify_receita_diaria` → tabela BQ | `shopify_diario` |
| `cliente_key` → coluna BQ | `cliente` |
| `data_inicio`/`data_fim` → coluna BQ | `data` |
| `valor_compras` → coluna BQ | `receita` |
| `pedidos_acima_300` → coluna BQ | `pedidos_gt300` |
| `CAST(x AS FLOAT)` | `CAST(x AS FLOAT64)` |
| `CAST(x AS INTEGER)` | `CAST(x AS INT64)` |
| `DATE(col, '-N days')` | `DATE_SUB(col, INTERVAL N DAY)` |
| `? posicionais` | `@p0, @p1, ...` |
| `HAVING SUM(x)` com múltiplas agg | substituído por alias do SELECT |
| `Decimal` retornado pelo BQ | normalizado para `float` |
