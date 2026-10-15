# Sync Diário de Sessões Shopify (ShopifyQL funil)

**Commit:** 1a355ce  
**Mensagem:** feat: sync diário de sessões Shopify (ShopifyQL funil)

## Arquivos Alterados

| Arquivo | Tipo |
|---------|------|
| `scripts/sync_shopify_sessions.py` | Criado |

## Systemd criado

| Arquivo | Conteúdo |
|---------|----------|
| `/etc/systemd/system/guara-shopify-sessions.service` | Executa `sync_shopify_sessions.py --dias 8` |
| `/etc/systemd/system/guara-shopify-sessions.timer` | Diário 12h30 UTC (09h30 BRT) |

## O que foi feito

Script de sync diário via ShopifyQL (API 2025-01) para SO e POD.

### Colunas disponíveis via ShopifyQL `FROM sessions` (2025-01)
| Campo ShopifyQL | Coluna DB | Nota |
|-----------------|-----------|------|
| `sessions` | `sessoes` | Total de sessões |
| `sessions_that_reached_checkout` | `sessoes_checkout` | Direto da API |
| `conversion_rate` | `taxa_conversao` | 0.0–1.0 |
| derivado | `sessoes_compra` | `round(sessoes × conv_rate)` |
| — | `sessoes_com_carrinho` | **NULL** — não existe no ShopifyQL |
| — | `is_bot_suspect` | 1 se sessões > 3× mediana 14 dias |

> `add_to_cart` e equivalentes não existem em nenhuma versão da API testada
> (2024-01, 2024-07, 2025-01, 2025-04). Campo deixado como NULL explícito.

### Detecção de bot — 08/10/2026 (SO)
- Sessões: **8.923** vs mediana dos 14 dias anteriores: **1.596** (5,6×)
- Checkout: 157 (valor normal — bot não chegou ao checkout)
- Taxa de conversão: **1,00%** (contra ~5% de média)
- Marcado automaticamente como `is_bot_suspect = 1`

## Dados gravados (backfill 01/09 → 14/10)

### Source OF
- 44 dias gravados
- Média sessões (excl. bot): ~1.800/dia
- Taxa conversão média: ~5,3%

### POD Kombucha
- 44 dias gravados
- **Atenção**: checkout e conversão muito baixos (0-4 checkouts/dia em 2k sessões)
- Pode ser limitação de permissão do token (read_analytics) — a verificar

## Uso manual (backfill)
```bash
cd agente-performance
venv/bin/python scripts/sync_shopify_sessions.py --data-inicio 2026-08-01 --data-fim 2026-09-01
venv/bin/python scripts/sync_shopify_sessions.py --cliente source_of --dias 30
```
