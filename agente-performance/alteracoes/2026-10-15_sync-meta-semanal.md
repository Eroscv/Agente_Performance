# Sync Semanal Meta campaign_totals + ATTRIBUTION_WINDOWS

**Commit:** c490852  
**Mensagem:** feat: sync semanal Meta campaign_totals (seg-dom fechado) + constante ATTRIBUTION_WINDOWS

## Arquivos Alterados

| Arquivo | Tipo |
|---------|------|
| `scripts/sync_meta_semanal.py` | Criado |
| `meta_ads/loader.py` | Alterado |

## O que foi feito

### 1. `scripts/sync_meta_semanal.py` (novo)
Script de sync semanal que carrega `campaign_totals` para **todos os clientes Meta ativos**
com o período **segunda–domingo fechado** da semana anterior.

- Cálculo automático da semana anterior (sem intervenção manual)
- `--semana-inicio YYYY-MM-DD` para backfill de semanas específicas
- `--dry-run` para validar sem gravar
- `--cliente` para rodar só um cliente
- Valida que a semana já está fechada (rejeita semana aberta)
- Dual-write BigQuery incluso (silencioso em falha)
- Registra runs no `cron_log`

### 2. `meta_ads/loader.py`
Extraída a constante `ATTRIBUTION_WINDOWS = '["7d_click","1d_view"]'` compartilhada
entre `buscar_insights_periodo` (ads_diario) e `buscar_totais_campaign`.

**Antes:** string `'["7d_click","1d_view"]'` duplicada em 2 lugares (risco de divergência)  
**Depois:** constante única `ATTRIBUTION_WINDOWS` — ambas tabelas garantidamente iguais

## Systemd criado

| Arquivo | Conteúdo |
|---------|----------|
| `/etc/systemd/system/guara-meta-semanal.service` | Executa `sync_meta_semanal.py` |
| `/etc/systemd/system/guara-meta-semanal.timer` | Toda segunda 09h UTC (06h BRT) |

## Validação executada

Semana 41 (05–11/out/2026) carregada com sucesso:
- source_of: 6 campanhas | R$ 51.391 invest | R$ 202.157 receita ✅
- pod_kombucha: 5 campanhas | R$ 33.276 invest | R$ 120.205 receita ✅
- botica: 2 campanhas | R$ 574 invest | R$ 313 receita ✅
- BQ MERGE OK nos 3 clientes ✅

## Impacto

| Aspecto | Antes | Depois |
|---------|-------|--------|
| Carga de campaign_totals | Manual/ad-hoc | Automática toda segunda 06h BRT |
| Janela de atribuição | Definida em 2 lugares independentes | Constante única `ATTRIBUTION_WINDOWS` |
| Cobertura | Dependia do loader diário (window móvel) | Semana fechada seg–dom garantida |
