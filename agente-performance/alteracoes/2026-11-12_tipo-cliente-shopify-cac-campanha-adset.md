# 2026-11-12 — Tipo de cliente da Shopify por pedido e CAC por campanha/adset (Source Of)

## Ingestão

- `scripts/sync_pedidos_diario.py` (Source Of): grava `tipo_cliente` ('novo' se `customer.orders_count` = 1,
  senão 'recorrente') e `customer_orders_count` por pedido. É o mesmo critério e a mesma chamada
  (`customers.json?ids=`) do resumo oficial (`shopify/sync.py`). Pedido sem customer, ou lote que falhou, fica nulo.
- A POD não grava (a regra dela é a do Colab, no gold).

## BigQuery (aplicado manualmente, aditivo; falta registrar com `scripts/apply_bq_sql.py` depois do commit, revisor Miguel)

- Bronze: `pedidos_diario.tipo_cliente`, `customer_orders_count` (`bigquery/scripts/bronze/ddl_pedidos_tipo_cliente.sql`).
- Silver: as mesmas colunas em `ing_shopify_pedidos` (`ddl_silver.sql`); o `load_silver()` já carrega por `SELECT *`.
- Gold: nova tabela `correlacao.fato_cac_campanha_adset` (investimento, pedidos, novos, recorrentes, CAC, ROAS e taxa
  de recorrência por dia x campanha x adset) no `load_gold()`. Catálogo: `bigquery/docs/data_catalog_gold.md` seção 7.
- Verificação de qualidade gold 12: investimento da tabela = Meta de `fato_performance_diaria`, por dia.
- Backfill: `sync_pedidos_diario.py --cliente source_of --data-inicio 2026-08-01`.

## Conferência (01–11/11, Source Of)

- Investimento Meta: R$ 84.299,82 nas duas tabelas (idêntico).
- Novos por dia (todos os pedidos pagos, 01/10 a 11/11): bate com `shopify_diario.novos_clientes` em 25 dos 42 dias.
  Os 17 que diferem têm 1 a 3 novos a menos que o oficial (até ~5%), quase todos de outubro. Motivo: o backfill usa o
  `orders_count` de hoje, e quem comprou de novo depois virou `recorrente`; o oficial guardou a contagem da época.
  De agora em diante cada dia é gravado pelo mesmo sync e congela depois da janela de 8 dias, então o critério é o mesmo.
- Meta: 691 novos e 1.113 pedidos contra 696 e 1.127 do oficial (falta o `referring_site`).
