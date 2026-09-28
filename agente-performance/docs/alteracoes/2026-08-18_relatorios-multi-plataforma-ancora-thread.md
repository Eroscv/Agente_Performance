# Alteração: Relatórios separados por plataforma — Source OF

**Commit:** `ddb00ac`  
**Data:** 2026-08-18

## Arquivos Alterados

- `scripts/cron_todos_relatorios.py`
- `notion/historico.py`
- `relatorio/builder_diario.py`
- `clientes.json`

## O que foi alterado

### Estrutura nova no Slack: âncora + threads

Antes: 1 mensagem única por cliente com todas as fontes misturadas.

Depois (Source OF):
```
📊 Source OF · Relatório Diário
   17/08/2026 → 17/08/2026
   📡 Fontes: Google Ads · Meta Ads · Shopify
      └── reply: 📊 Google Ads · dados + link Notion
      └── reply: 📊 Meta Ads · dados + link Notion  
      └── reply: 📊 Shopify · dados + link Notion
      └── reply: ⚠️ Fontes com falha: [se houver]
```

### Noção: páginas separadas por plataforma

O cache agora inclui `plataforma` na chave — cada fonte cria sua própria página no Notion em vez de sobrescrever a mesma.

### Detecção automática de dados disponíveis

Antes de gerar qualquer relatório, o sistema verifica se há dados para o período em cada tabela:
- `source_of_gads_diario` → Google Ads
- `source_of_meta_ads_diario` → Meta Ads
- `source_of_shopify_diario` → Shopify

Só gera relatório para fontes com dados. Se uma falhar, as demais continuam.

### Diagnóstico do loader Meta Ads

A Meta API está com instabilidade intermitente (HTTP 500 / "Service temporarily unavailable"). Esse é um problema externo. Com o loader tolerante a falhas (correção anterior), os dados do Google Ads e Shopify são sincronizados mesmo quando a Meta falha. Os dados da Meta no banco ainda são do último sync bem-sucedido (17/08).

## Impacto

| Cenário | Antes | Depois |
|---------|-------|--------|
| Estrutura Slack | 1 msg misturada | Âncora + 3 threads separadas |
| Notion | 1 página sobrescrita | 3 páginas independentes |
| Meta API instável | Todo relatório falha | Outras fontes continuam normalmente |
| Shopify | Incluído no relatório Meta | Relatório próprio independente |
