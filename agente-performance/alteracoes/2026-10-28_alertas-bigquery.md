# Alteração: Migração de alertas para BigQuery como fonte primária

**Data:** 2026-10-28
**Commit:** 986b586
**Autor:** Cesar (Agente)

## Arquivos alterados

| Arquivo | Tipo |
|---|---|
| `db/bq_reader.py` | Criado |
| `scripts/cron_todos_relatorios.py` | Atualizado |

## O que foi alterado

### `db/bq_reader.py` (novo)
Módulo de leitura analítica do BigQuery — espelho do `bq_writer.py` mas para leitura.

Funções públicas:
- `enabled()` → True se credenciais BQ configuradas
- `query_bq(sql, params)` → executa query com parâmetros `@p0, @p1, ...`
- `query_bq_one(sql, params)` → primeiro resultado
- `data_maxima_bq(cliente_key)` → data mais recente em `ads_diario`
- `alertas_roas_bq(cliente_key, threshold, janela_dias)` → campanhas com ROAS abaixo do threshold via `v_alertas_campanha`
- `campanhas_sem_conversao_bq(cliente_key, janela_dias)` → campanhas sem compras

### `scripts/cron_todos_relatorios.py`
Função `verificar_alertas()` migrada para BQ como fonte primária:

**Antes:** Lia de `campanhas_unificada` (DuckDB/SQLite local) — sem média 7d, sem status de alerta

**Depois:**
- Fonte primária: BigQuery via `v_alertas_campanha` (inclui `roas_media_7d` e `status_alerta`)
- Fallback: SQLite local se BQ indisponível (mantido para resiliência)
- Campo `fonte: "bigquery"` ou `"sqlite_fallback"` no objeto de alerta

Função `postar_alerta_ancorado()` também atualizada:
- Quando fonte=bigquery: exibe coluna "Média 7d" com ícone por status (🔴 crítico / 🟡 alerta)
- Rodapé exibe "Fonte: BigQuery ✅"

## Impacto

| Aspecto | Antes | Depois |
|---|---|---|
| Fonte dos dados | SQLite local (DuckDB) | BigQuery (`ads_diario` / `v_alertas_campanha`) |
| Média 7d | ❌ Não disponível | ✅ Calculada no BQ (OVER 7 PRECEDING) |
| Status de alerta | ❌ Não disponível | ✅ critico / alerta / destaque / novo |
| Resiliência | — | ✅ Fallback SQLite se BQ cair |
| Transparência no Slack | threshold apenas | threshold + fonte + média 7d |
