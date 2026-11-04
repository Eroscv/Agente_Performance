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
  níveis, Google), `correlacao.fato_pedidos` (canais Shopify: meta,
  google, direto, outros) e `guara_performance.dooki_pedidos_diario`
  (canal `yampi_checkout`, só para POD Kombucha).

| Coluna | Tipo | Descrição |
|---|---|---|
| `cliente_id` | STRING | FK para `dim_cliente.cliente_id`. |
| `data` | DATE | Dia de referência. |
| `canal` | STRING | `meta`, `google`, `direto`, `outros` ou `yampi_checkout`. |
| `investimento` | NUMERIC | Gasto em mídia paga no canal/dia (0 para canais orgânicos). |
| `cliques_link` | FLOAT64 | Cliques no link do anúncio (NULL para canais sem mídia). |
| `impressoes` | FLOAT64 | Impressões do anúncio (NULL para canais sem mídia). |
| `pedidos` | INT64 | Pedidos atribuídos ao canal/dia. |
| `receita` | FLOAT64 | Receita atribuída ao canal/dia. |
| `novos_clientes` | INT64 | Novos clientes no canal/dia. |
| `roas_real` | FLOAT64 | `receita / investimento` (NULL/divisão segura quando investimento = 0). |
