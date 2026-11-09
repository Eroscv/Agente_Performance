# 2026-11-09 — Estado e itens dos pedidos, dashboard Source Of

## Bigquery (aplicado com `scripts/apply_bq_sql.py`, commit 4d34396, revisor Miguel)

- Bronze: `pedidos_diario.estado` (UF de entrega) e nova tabela `pedido_itens`
  (`bigquery/scripts/bronze/ddl_pedido_itens.sql`). A tabela e a coluna foram criadas antes, direto,
  por uma execução manual do mesmo SQL (aditivo); a aplicação oficial registrou o mesmo arquivo em
  `correlacao.log_alteracoes_bq`.
- Silver: `ing_shopify_pedido_itens`, coluna `ing_shopify_pedidos.estado` e novo passo em `ingestao.load_silver()`.
- Verificações de qualidade da silver: 9 (item novo em 1 e 4, e pedido pago recente sem itens).
- Backfill desde 31/08 (Source Of e POD) com `sync_pedidos_diario.py --data-inicio 2026-08-31`.

## Shopify

- `shopify/client.py`: `_ORDER_FIELDS` passa a pedir `landing_site` e `referring_site`. Sem isso, o
  fallback de canal do resumo diário nunca rodava e os pedidos de 08 e 09/11 da Source Of caíram todos em Outros.

## Dashboard Source Of

- `scripts/gerar_dados_dashboard_source_of.py` gera `src/data.ts` a partir da silver/gold.
- Fonte menor nos valores grandes, 3 tabelas de criativos, CAC por criativo e por canal, gráfico diário de
  investimento e ROAS, checkouts por origem, pedidos por estado e por SKU, Instagram orgânico, ChatGPT e aba Recompra.
