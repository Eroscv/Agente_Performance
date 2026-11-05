# Naming Conventions — guara-db

Adaptado de [DataWithBaraa/sql-data-warehouse-project](https://github.com/DataWithBaraa/sql-data-warehouse-project/blob/main/02_docs/naming_conventions.md)
para o projeto `guara-db` (BigQuery). Documenta a convenção que **já estava
em uso** nas três camadas — este arquivo formaliza, não renomeia nada.

## Princípios gerais

- `snake_case`, minúsculas, português para nomes de entidade (o domínio é
  PT-BR: pedidos, clientes, campanhas).
- Um dataset BigQuery por camada: `guara_performance` (bronze),
  `ingestao` (silver), `correlacao` (gold).
- Evitar nomes reservados do GoogleSQL.

## Bronze (`guara_performance`)

`<entidade>`, nome da entidade como a fonte a expõe, sem prefixo de
sistema (diferente do repositório de referência, que usa
`<sistema>_<entidade>` porque mistura CRM + ERP num único dataset — aqui
cada entidade já é inequívoca: `shopify_diario`, `ads_diario`,
`pedidos_diario`, `abandoned_checkout_daily`, `dooki_pedidos`).

## Silver (`ingestao`)

`ing_<entidade>`, mesmo nome de entidade do bronze, prefixado com `ing_`
para deixar explícito a camada ao ler o nome da tabela isolado (ex: num
JOIN). Sem renomear colunas em relação ao bronze. Colunas derivadas só entram quando
reutilizadas por mais de uma consulta gold (`ing_ads_diario.nivel`) ou para
marcar qualidade (`dq_<regra>`, ex: `dq_cliques_todos_ausente`).

| Bronze | Silver |
|---|---|
| `shopify_diario` | `ing_shopify_resumo_diario` |
| `pedidos_diario` | `ing_shopify_pedidos` |
| `ads_diario` | `ing_ads_diario` (+ `nivel`) |
| `abandoned_checkout_daily` | `ing_checkout_abandonado_diario` |
| `abandoned_checkout_detail` | `ing_checkout_abandonado_detalhe` |
| `meta_campaign_totals` | `ing_meta_campaign_totals` |
| `novos_clientes_semanal` | `ing_novos_clientes_semanal` |
| `dooki_pedidos` | `ing_yampi_pedidos` |
| `shopify_clientes_historico` | `ing_shopify_clientes_historico` |
| `tickets_alerta` | `ing_tickets` |
| `metricas_sla` | `ing_metricas_sla` |

## Gold (`correlacao`)

`<categoria>_<entidade>`, mesma regra do repositório de referência:

| Padrão | Significado | Exemplos |
|---|---|---|
| `dim_` | Tabela de dimensão | `dim_cliente`, `dim_cliente_alias` |
| `fato_` | Tabela de fato | `fato_pedidos`, `fato_performance_diaria` |

Gold usa tabelas materializadas (`CREATE OR REPLACE TABLE` ou `MERGE`),
não views — diferente do repositório de referência, que usa views porque
seu silver já é o modelo final de 3FN. Aqui o gold agrega e junta dados de
múltiplas fontes (ads + Shopify + Dooki) por dia, o que é caro demais para
recalcular em toda leitura; materializar é a escolha certa em troca de
rodar `load_gold()` a cada sync.

### Colunas técnicas

Todas as tabelas (bronze, silver e gold) já carregam `criado_em`
(`TIMESTAMP`) marcando quando a linha foi gravada — equivalente ao
`dwh_create_date` do repositório de referência. Não foi renomeado para
não quebrar os scripts e dashboards que já leem essa coluna.

### Chaves substitutas (surrogate keys)

Não usadas nesta base: `fato_pedidos` e `fato_performance_diaria` são
chaveadas pelas chaves naturais do domínio (`order_id` + `cliente_id`;
`cliente_id` + `data` + `canal`), que já são únicas e estáveis — não há
necessidade de `ROW_NUMBER()` surrogate keys como no repositório de
referência (lá, `customer_key`/`product_key` existem porque as chaves
naturais do CRM/ERP vêm de sistemas diferentes e podem colidir).

## Stored procedures

`load_<camada>`, uma por camada, no dataset correspondente:

- `ingestao.load_silver()` — bronze → silver
  (`bigquery/scripts/silver/proc_load_silver.sql`)
- `correlacao.load_gold()` — silver → gold
  (`bigquery/scripts/gold/proc_load_gold.sql`)

Chamadas por `scripts/rebuild_bq_layers.py`, nessa ordem, no fim de
`scripts/cron_master_sync.sh`.
