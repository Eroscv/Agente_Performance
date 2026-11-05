# Estado atual do BigQuery — guara-db (05/11/2026)

Retrato do que existe hoje, depois da reestruturação do medalhão
(`alteracoes/2026-11-05_medalhao-silver-merge.md`). Para o desenho e as regras,
veja `data_architecture.md`; para as colunas do gold, `data_catalog_gold.md`;
para nomes, `naming_conventions.md`.

## Fluxo

```
APIs (Shopify, Meta, Google Ads, Yampi/Dooki)
   │  scripts de sync (Python, a cada 6h: scripts/cron_master_sync.sh)
   ▼
BRONZE  guara_performance   cru, upsert por chave, sem regra de negócio
   │  CALL ingestao.load_silver()   MERGE idempotente + limpeza
   ▼
SILVER  ingestao            ing_<entidade>, limpo, um registro por chave natural
   │  CALL correlacao.load_gold()   só lê a silver
   ▼
GOLD    correlacao          dim_/fato_, pronto para dashboard e relatórios
```

`scripts/rebuild_bq_layers.py` é o último passo do cron e chama silver e depois gold.

## Bronze — `guara_performance`

| Tabela | Linhas | Origem / chave |
|---|---:|---|
| `ads_diario` | 23.534 | Meta + Google Ads, por anúncio/campanha e dia |
| `pedidos_diario` | 8.804 | Shopify, um pedido por linha (`cliente`, `order_id`) |
| `shopify_diario` | 628 | resumo diário Shopify (`cliente`, `data`) |
| `abandoned_checkout_daily` / `_detail` | 281 / 1.815 | checkouts abandonados |
| `dooki_pedidos` | 7.309 | Yampi das 3 lojas, um pedido por linha (`loja`, `order_id`) |
| `shopify_clientes_historico` | 38.119 | primeiro pedido por cliente da Shopify da POD |
| `meta_campaign_totals` | 72 | totais de campanha Meta |
| `novos_clientes_semanal` | 12 | novos clientes por semana |
| `tickets_alerta` | 50 | tickets das reuniões (subsistema de transcrição) |
| `metricas_sla` | 2.404 | SLA de tickets (subsistema de transcrição) |
| `analise_reunioes`, `transcricao` | 35, 4 | transcrição |
| `dooki_pedidos_diario` | 188 | legado (total por dia), não é mais lido |

As views `v_*` (dashboards, relatórios, agente) agora leem a silver (`ingestao`), exceto `v_analise_reunioes`
(sem silver, é transcrição). `dooki_pedidos_diario` (legado) não é lida por nenhuma view.

## Silver — `ingestao`

Carregada por `ingestao.load_silver()` (`scripts/silver/proc_load_silver.sql`) via
`ingestao.sp_upsert`: MERGE pela chave natural, atualiza só o que mudou, remove o que
saiu do bronze, aborta se a fonte vier vazia. Rodar duas vezes seguidas não altera nada.
DDL das tabelas que só o MERGE alimenta: `scripts/silver/ddl_silver.sql`.

| Tabela | Linhas | Fonte | Chave | Limpeza |
|---|---:|---|---|---|
| `ing_shopify_pedidos` | 8.804 | `pedidos_diario` | cliente, order_id | e-mail/utm minúsculos, vazio→NULL |
| `ing_shopify_resumo_diario` | 628 | `shopify_diario` | cliente, data | — |
| `ing_checkout_abandonado_diario` | 281 | `abandoned_checkout_daily` | cliente, data, utm_source_norm | — |
| `ing_checkout_abandonado_detalhe` | 1.815 | `abandoned_checkout_detail` | cliente, checkout_id | e-mail minúsculo |
| `ing_ads_diario` | 23.534 | `ads_diario` | cliente, plataforma, data, campaign/adset/ad, account | `nivel`; cliques totais ausentes → NULL + `dq_cliques_todos_ausente` |
| `ing_meta_campaign_totals` | 72 | `meta_campaign_totals` | cliente, período, campanha, conta | — |
| `ing_novos_clientes_semanal` | 12 | `novos_clientes_semanal` | cliente, semana_inicio | — |
| `ing_yampi_pedidos` | 7.309 | `dooki_pedidos` | loja, order_id | sem `novo_cliente`; e-mail/utm minúsculos |
| `ing_shopify_clientes_historico` | 38.119 | `shopify_clientes_historico` | cliente, customer_id, email | e-mail minúsculo |
| `ing_tickets` | 46 | `tickets_alerta` | ticket_id | versão mais recente por ticket |
| `ing_metricas_sla` | 2.404 | `metricas_sla` | gerado_em, janela_dias, tipo | — |
| `log_cargas` | — | controle | — | uma linha por tabela a cada carga |

## Gold — `correlacao`

Carregada por `correlacao.load_gold()` (`scripts/gold/proc_load_gold.sql`), lendo só a silver.

| Tabela | Linhas | O que é |
|---|---:|---|
| `dim_cliente`, `dim_cliente_alias` | 8, 19 | cadastro de clientes e aliases de UTM |
| `fato_pedidos` | ~8.925 | pedidos pagos com canal (Shopify + poddi da Yampi); MERGE insert-only (guarda correções históricas) |
| `fato_pedidos_pod` | 7.563 | POD, um pedido por linha: Shopify (pod.com.br) + Yampi poddi, pelas regras do Colab da Bia |
| `fato_performance_diaria` | 1.170 | investimento, receita, ROAS e novos por cliente/canal/dia; `investimento_ajustado` (Meta ×1,1215) |
| `fato_tickets`, `alertas_qualidade_dados`, `qualidade_dados_checks` | 46, 8, 14 | subsistema de tickets |
| `fato_cac` | 1 | placeholder, fora de escopo |
| `fn_canal_pod` | — | função: canal pelo `utm_source` |

Views para o dashboard: `v_dash_overview_corrigida`, `v_dash_campaigns_corrigida`, `v_dash_shopify_corrigida`.

## Regras da POD Kombucha (resumo)

- Pago: transação capturada e não cancelada, e status atual fora de cancelled/refused.
  Pedido Shopify sem par na Yampi vale `financial_status = paid`.
- Receita sem frete. A Yampi pod-kombucha são os mesmos pedidos da Shopify e não soma por cima.
- Canal pelo `utm_source`: Meta, Google, Outros (revi, e-mail, sms, shopper) ou Orgânico/Direto.
- Cliente novo: pod.com.br = primeiro pedido da vida na Shopify (5 min); poddi = e-mail que nunca
  comprou no pod.com.br e primeiro pedido na poddi desde 01/10.
- Imposto Meta ×1,1215 em `investimento_ajustado`; o dashboard ainda aplica 1,12.

## Verificação

`tests/quality_checks_silver.sql` (6 verificações) e `tests/quality_checks_gold.sql` (11):
cada consulta deve voltar zero linhas. Rodar depois de todo `load_silver`/`load_gold`.

## Backups

Snapshots `*_bkp_20261104` e `*_bkp_20261105` (anteriores à reestruturação) e
`correlacao.fato_*_pre_medalhao` (expiram em 30 dias).
