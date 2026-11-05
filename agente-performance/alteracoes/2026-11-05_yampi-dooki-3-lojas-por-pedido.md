# POD Kombucha: Yampi/Dooki por pedido e gold alinhado ao Colab (05/11/2026)

## Problema

A Bia comparou o dashboard (que lê `correlacao.*`) com o Colab `pod_central_performance_13` em
01–04/11: CAC +22%, MER bruto −34%, ROAS pago Meta −21%. Ela mandou o tutorial "Como calculamos o
CAC — Pod Kombucha" e o CSV `pagos_0111_colab.csv` (102 pedidos pagos de 01/11, linha a linha).

## Causas (todas verificadas contra BigQuery, API da Yampi/Shopify e o CSV)

1. **A POD vende em 3 lojas Yampi** (`poddi`, `pod-kombucha`, `pod8`) com credenciais próprias e o `.env`
   tinha `DOOKI_BASE_URL` duplicada: só uma loja era sincronizada.
2. **Pedido era "pago" só por status `paid`**, e a carga gravava o total do dia. Pedido que avança
   (`paid` → `on_carriage` → `delivered`) sumia da receita. Agora a carga é **por pedido** (upsert por
   `loja + order_id`) com a regra do Colab: transação capturada e não cancelada **e** status atual fora
   de `cancelled`/`refused`.
3. **A Yampi pod-kombucha são os mesmos pedidos da Shopify** (`metadata.platform_order_number` =
   número do pedido Shopify). O gold somava as duas por cima uma da outra. Agora a pod-kombucha só dá o
   status de pagamento; sem par na Yampi (6 pedidos em 01/11) vale `financial_status = paid` da Shopify.
4. **Receita sem frete**: a poddi entrava com `value_total` (com frete). Colab usa `current_subtotal_price`
   (Shopify) e `value_products − value_discount` (Yampi).
5. **Canal**: a poddi caía num canal único `yampi_checkout`. Agora o `utm_source` define
   meta/google/outros/direto (regra de substring do Colab, `correlacao.fn_canal_pod`).
6. **Cliente novo**: o cálculo antigo só via o histórico do BigQuery (75 contra 65 do Colab em 01/11).
   Agora: Shopify = primeiro pedido do cliente; poddi = e-mail que nunca comprou no pod.com.br e é o
   primeiro pedido do e-mail na poddi.
7. **Imposto Meta**: o BQ já usava 1,1215. O dashboard mostra R$ 37.135 = Google + Meta × **1,12**
   (fora do BQ). Use `investimento_ajustado`.

O "resíduo de 6 pedidos / R$ 226" que eu atribuí a cancelados era outra coisa: eu comparava a Yampi
pod-kombucha (84) + poddi (12) **com frete** contra Shopify (90) + poddi (12) **sem frete** do Colab.

## O que mudou

- `dooki/sync.py` / `dooki_pedidos`: colunas `pago`, `captured_at`, `receita_sem_frete`,
  `customer_email`, `shopify_order_number`; schema evolui sozinho (`ADD COLUMN IF NOT EXISTS`).
- `scripts/sync_pedidos_diario.py` / `pedidos_diario`: `order_name`, `current_subtotal_price`,
  `customer_email`.
- `scripts/sync_shopify_clientes_historico.py` → `guara_performance.shopify_clientes_historico`
  (primeiro pedido por cliente). A Shopify só devolve 60 dias de pedidos sem `read_all_orders`; o
  primeiro pedido de cliente antigo é inferido por `orders_count` (reproduz 90/90 as flags do CSV).
- `bigquery/scripts/gold/proc_load_gold.sql`: `fn_canal_pod`, nova `correlacao.fato_pedidos_pod`
  (espelha o CSV da Bia) e `fato_performance_diaria` com `investimento_ajustado`, `pedidos_criados` e
  `receita_criada` (POD). Canal `yampi_checkout` aposentado. Source Of/Botica não mudaram.
- `scripts/cron_master_sync.sh`: Yampi com `--dias 45` (reprocessa mudança de status) e o histórico
  de clientes antes do rebuild.

## Validação

01/11, pedido a pedido contra `pagos_0111_colab.csv`: 102 pedidos pagos, R$ 30.537,65, canais
(Meta 68 / Orgânico-Direto 20 / Google 12 / Outros 2) e fonte de pagamento idênticos; cliente novo 71.

| 01–04/11 | Colab | `fato_performance_diaria` |
|---|---|---|
| Pedidos pagos | 445 | 445 |
| Receita paga | R$ 134.009 | R$ 134.009 |
| Novos clientes | 286 | 286 |
| Pedidos / receita criados | 516 / R$ 155.924 | 516 / R$ 155.924 |
| Meta: pedidos / receita UTM / novos | 253 / R$ 73.166 / 196 | 253 / R$ 73.166 / 196 |
| Meta ROAS pago | 2,34x | 2,34x |
| Investimento (com imposto) | R$ 37.184 | R$ 37.177 |
| CAC conta / CAC Meta | R$ 130,01 / R$ 159,70 | R$ 129,99 / R$ 159,65 |

Outubro: 2.359 pedidos e R$ 658.177 (iguais), investimento R$ 205.144,07 (igual), novos 1.482 contra
1.479 (CAC R$ 138,42 contra R$ 138,70).

## Fechado

- **Shopify-only reembolsado:** conferi os 250 pedidos sem par na Yampi dos últimos 58 dias na Shopify:
  todos continuam `paid`. Sem impacto, sem código.
- **Conta Meta "Nova loja":** está carregada nos 4 dias de 01–04/11 (R$ 1.016,72 contra R$ 26.884,53 da
  conta principal; soma R$ 27.901,25).
