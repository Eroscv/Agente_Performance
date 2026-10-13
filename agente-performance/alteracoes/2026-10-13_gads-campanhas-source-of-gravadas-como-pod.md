# Fix: Campanhas Source OF gravadas como pod_kombucha no Google Ads (13/10/2026)

## Commit
`db2e917` — fix(gads): guarda de conta no loader + customer.id na QUERY

## Arquivos alterados
- `google_ads/loader.py`

## O que aconteceu

5 campanhas da conta Source OF (`customer_id=4311260590`) estavam sendo gravadas na tabela
`pod_kombucha_gads_diario` e espelhadas no BigQuery como `cliente='pod_kombucha'`:

| campaign_id | campanha | conta real |
|---|---|---|
| 21877133703 | PMax Geral | Source OF (4311260590) |
| 23745716805 | [SEARCH] Hidratante Corporal Pele Ressecada | Source OF (4311260590) |
| 23314457019 | [SEARCH] Branded | Source OF (4311260590) |
| 23096632725 | [Guará] Shopping - Branded | Source OF (4311260590) |
| 24242405738 | [Guara] Shopping - Gel Creme Facial | Source OF (4311260590) |

**Impacto:** R$ 1.348 a mais no Google Ads da POD na semana (confirmado pelo usuário).
**Período afetado:** 07/08/2026 – 06/10/2026 (264 linhas).

## Causa raiz

O loader da POD (`gads_customer_id=6123672195`) busca dados na API do Google Ads usando o MCC
`2208127533` como login. Em algumas situações (provavelmente quando o MCC tem acesso cruzado),
a API retornava campanhas de subcontas vizinhas sem filtrar pelo `customer_id` solicitado.

A query GAQL não incluía `customer.id` no SELECT — sem esse campo, era impossível verificar
de qual conta cada linha vinha.

## Fix aplicado

1. **Adicionado `customer.id` ao SELECT da QUERY GAQL:**
   ```sql
   SELECT customer.id, campaign.id, campaign.name, ...
   ```

2. **Guarda no loop de resultados:**
   ```python
   row_cid = str(row.customer.id) if row.customer.id else None
   if row_cid and row_cid != str(customer_id):
       logger.warning("[%s] campanha ignorada — pertence à conta %s, não %s: campaign_id=%s (%s)",
                      customer_id, row_cid, customer_id, c.id, c.name)
       skipped += 1
       continue
   ```

## Limpeza aplicada

- SQLite `pod_kombucha_gads_diario`: **264 linhas removidas** (campaign_id IN das 5 campanhas)
- BigQuery `ads_diario` (cliente=pod_kombucha, plataforma=google): **264 linhas removidas**

## Verificação

Após a limpeza, nenhuma das 5 campanhas existe mais em nenhum dos dois storages como pod_kombucha.
O próximo sync do `gads-sync-horario` gravará apenas campanhas legítimas da conta `6123672195`.

*Corrigido por Cesar (Agente de Performance Guará) em 13/10/2026.*
