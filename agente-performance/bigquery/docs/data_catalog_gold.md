# Data Catalog — Gold Layer (`correlacao`)

## 1. `correlacao.dim_cliente`

- **Propósito:** cadastro dos clientes atendidos (Source Of, POD Kombucha,
  Botica Ouro Preto, ...).
- **Atualização:** estático, mantido manualmente (não reconstruído por
  `load_gold()`).

| Coluna | Tipo | Descrição |
|---|---|---|
| `cliente_id` | STRING | Chave natural usada em todo o restante do gold (`fato_pedidos.cliente_id`, `fato_performance_diaria.cliente_id`). |
| `nome_exibicao` | STRING | Nome legível do cliente para relatórios/dashboards. |
| `criado_em` | TIMESTAMP | Quando o registro foi criado. |

## 2. `correlacao.dim_cliente_alias`

- **Propósito:** mapeia variações de nome/alias vistas em `utm_source` ou
  `source_name` de volta para o `cliente_id` canônico de `dim_cliente`.
- **Atualização:** estático, mantido manualmente.

| Coluna | Tipo | Descrição |
|---|---|---|
| `alias` | STRING | Variante de nome observada na fonte (ex: um `utm_source` específico). |
| `cliente_id` | STRING | `cliente_id` canônico correspondente, em `dim_cliente`. |
| `origem` | STRING | De onde veio esse alias (ex: `shopify`, `meta`). |

## 3. `correlacao.fato_pedidos`

- **Propósito:** pedidos pagos por cliente com canal de aquisição
  classificado, base de ROAS pago/CAC por anúncio quando cruzado com
  `fato_performance_diaria`.
- **Atualização:** `correlacao.load_gold()` — **MERGE incremental**, só
  insere `order_id` novos (nunca faz UPDATE em linhas existentes).
- **Fonte:** `guara_performance.pedidos_diario` (bronze), filtrado por
  `status = 'paid'`.
- **Nota de qualidade conhecida:** o histórico anterior a esta
  reestruturação tem uma divergência de <1% entre `novo_cliente` e
  `novo_cliente_raw` que não pôde ser reconstruída a partir do schema —
  por isso o MERGE é insert-only, para não sobrescrever essa correção
  manual antiga com o cálculo simplificado.

| Coluna | Tipo | Descrição |
|---|---|---|
| `order_id` | STRING | ID do pedido na loja de origem. |
| `cliente_id` | STRING | FK para `dim_cliente.cliente_id`. |
| `cliente_nao_canonizado` | BOOL | `TRUE` se `cliente_id` ainda não passou por `dim_cliente_alias`. |
| `data_brt` | DATE | Data do pedido em horário de Brasília. |
| `created_at_utc` | TIMESTAMP | Data/hora de criação em UTC. |
| `status` | STRING | Status do pedido (`paid` para tudo que entra aqui). |
| `valor_pago` | FLOAT64 | Valor pago pelo pedido. |
| `desconto` | FLOAT64 | Desconto aplicado. |
| `frete` | FLOAT64 | Valor de frete. |
| `novo_cliente` | BOOL | Se é primeira compra do cliente. |
| `novo_cliente_raw` | BOOL | Valor de `novo_cliente` antes de qualquer correção manual (hoje, igual a `novo_cliente` para linhas novas). |
| `customer_id` | STRING | ID do cliente na loja de origem. |
| `utm_source` / `utm_campaign` / `utm_content` | STRING | Atribuição de UTM do pedido. |
| `source_name` | STRING | Canal de venda reportado pela loja (ex: `web`, `pos`). |
| `canal` | STRING | Classificação derivada de `utm_source`: `meta`, `google`, `direto` ou `outros` (ver regra em `proc_load_gold.sql`). |

## 4. `correlacao.fato_performance_diaria`

- **Propósito:** investimento, pedidos, receita e ROAS por
  cliente/canal/dia — a tabela que a maioria dos dashboards e relatórios
  consulta diretamente.
- **Atualização:** `correlacao.load_gold()` — full rebuild
  (`CREATE OR REPLACE TABLE`).
- **Fontes:** `ingestao.ing_ads_diario` (nível anúncio, Meta; todos os
  níveis, Google), `correlacao.fato_pedidos` (Source Of / Botica) e
  `correlacao.fato_pedidos_pod` (POD Kombucha, metodologia do Colab).
  O canal `yampi_checkout` foi aposentado: os pedidos da Yampi entram nos
  canais `meta`/`google`/`outros`/`direto` pelo `utm_source`.

| Coluna | Tipo | Descrição |
|---|---|---|
| `cliente_id` | STRING | FK para `dim_cliente.cliente_id`. |
| `data` | DATE | Dia de referência. |
| `canal` | STRING | `meta`, `google`, `outros` ou `direto` (Orgânico/Direto). |
| `investimento` | NUMERIC | Gasto em mídia paga no canal/dia (0 para canais orgânicos). |
| `cliques_link` | FLOAT64 | Cliques no link do anúncio (NULL para canais sem mídia). |
| `impressoes` | FLOAT64 | Impressões do anúncio (NULL para canais sem mídia). |
| `pedidos` | INT64 | Pedidos **pagos** atribuídos ao canal/dia. |
| `receita` | FLOAT64 | Receita paga do canal/dia. POD: sem frete (Colab). Source Of/Botica: `valor_pago`. |
| `novos_clientes` | INT64 | Novos clientes que pagaram, no canal/dia. |
| `roas_real` | FLOAT64 | `receita / investimento` (investimento bruto, sem imposto). Para ROAS pago como no Colab use `receita / investimento_ajustado`. |
| `investimento_ajustado` | FLOAT64 | `investimento` com imposto: Meta × 1,1215 (12,15%), Google × 1. Fonte única do fator para dashboards. |
| `pedidos_criados` | INT64 | Só POD: pedidos criados no canal/dia, qualquer status (NULL para os demais clientes). |
| `receita_criada` | FLOAT64 | Só POD: receita sem frete de todos os pedidos criados (NULL para os demais clientes). |

## 5. `correlacao.fato_pedidos_pod`

- **Propósito:** um pedido por linha da POD Kombucha na metodologia do Colab
  da Bia (tutorial "Como calculamos o CAC — Pod Kombucha"). Espelha as colunas
  do CSV `pagos_*_colab.csv`, então dá para conferir pedido a pedido.
- **Atualização:** `correlacao.load_gold()` — full rebuild.
- **Fontes:** `guara_performance.pedidos_diario` (Shopify),
  `guara_performance.dooki_pedidos` (Yampi: lojas `pod_kombucha` e `poddi`),
  `guara_performance.shopify_clientes_historico` (primeiro pedido por cliente).
- **Regras:**
  - **pod.com.br** = pedidos Shopify ligados à Yampi pod-kombucha por
    `order_name` = `shopify_order_number`. A Yampi pod-kombucha são os mesmos
    pedidos: ela só dá o status de pagamento, nunca soma receita por cima.
    Sem par na Yampi vale `financial_status = paid` da Shopify
    (`fonte_pagamento = 'shopify_fallback'`).
  - **poddi.com.br** = Yampi poddi, receita própria (`fonte_pagamento = 'yampi_nova'`).
  - **Pago** = transação capturada e não cancelada e status atual fora de
    `cancelled`/`refused`. Cancelado nunca conta, mesmo com captura.
  - **Receita** sem frete: Shopify `current_subtotal_price`; Yampi
    `value_products − value_discount`.
  - **Canal** pelo `utm_source` (`fn_canal_pod`): facebook/meta/fb/ig/instagram
    → `meta`; google → `google`; revi/email/sms/shopper → `outros`; vazio ou
    qualquer outro → `direto`. Substring, igual ao Colab ("ig"/"fb" casam em
    qualquer parte do texto).
  - **Cliente novo** pod.com.br: o primeiro pedido do cliente na Shopify é este
    pedido (tolerância de 5 min). poddi: e-mail preenchido, nunca comprou no
    pod.com.br antes e é o primeiro pedido do e-mail na poddi (desde 01/10).
    Só conta no CAC se o pedido também for `pago`.
  - **Dia** = criação do pedido no horário de São Paulo.

| Coluna | Tipo | Descrição |
|---|---|---|
| `loja` | STRING | `pod.com.br` ou `poddi.com.br`. |
| `order_id` | STRING | ID Shopify (pod.com.br), `yampi-<id>` (pedido só na Yampi) ou `poddi-<id>`. |
| `order_name` | STRING | `#55315` (Shopify) ou `poddi-<id>`. |
| `data_brt` | DATE | Data de criação em horário de São Paulo. |
| `created_at_utc` | TIMESTAMP | Criação em UTC. |
| `status_atual` | STRING | Status atual na Yampi (NULL em `shopify_fallback`). |
| `fonte_pagamento` | STRING | `dooki`, `shopify_fallback` ou `yampi_nova`. |
| `pago` | BOOL | Regra de pago acima. |
| `receita_sem_frete` | FLOAT64 | Receita do pedido sem frete. |
| `total_price` | FLOAT64 | Valor total com frete. |
| `frete` | FLOAT64 | Frete. |
| `customer_id` | STRING | ID do cliente na Shopify (NULL na poddi). |
| `customer_email` | STRING | E-mail em minúsculas. |
| `novo_cliente` | BOOL | Regra de cliente novo acima. |
| `canal` | STRING | `meta`, `google`, `outros` ou `direto`. |
| `utm_source` / `utm_medium` / `utm_campaign` / `utm_content` | STRING | UTMs (Shopify quando há pedido Shopify; senão Yampi). `utm_content` = ID do anúncio na Meta. |

**Limite conhecido:** sem o escopo `read_all_orders` a Shopify só devolve 60
dias de pedidos; o primeiro pedido de clientes antigos é inferido por
`orders_count` (ver `scripts/sync_shopify_clientes_historico.py`). A flag `novo_cliente` da
POD é confiável a partir de 2026-09-06, primeiro dia coberto pela carga inicial; antes disso,
quem comprou pela primeira vez entre 01 e 05/09 e recomprou depois aparece como recorrente.
