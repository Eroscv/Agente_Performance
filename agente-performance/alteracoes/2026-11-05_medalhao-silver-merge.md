# Medalhão: silver por MERGE, limpa, e gold só lendo silver (05/11/2026)

## Problema (apontado em revisão)
- A silver era cópia do bronze recriada inteira (DROP/CREATE) a cada carga; o
  documento prometia MERGE idempotente.
- A Yampi/Dooki e o histórico de clientes ficavam só no bronze, e o gold lia o bronze.
- Sem limpeza: 14.139 linhas de anúncios Meta com `cliques_todos` (0) menor que
  `cliques_link`; texto sujo (e-mail/utm em maiúscula, vazio).
- `ing_tickets` e `ing_metricas_sla` fora do `load_silver`.

## O que mudou
- `bigquery/scripts/silver/proc_load_silver.sql`: `ingestao.sp_upsert` (MERGE
  idempotente por chave natural, só atualiza o que mudou, remove o que saiu do
  bronze, aborta se a fonte vier vazia) e `load_silver()` com 11 tabelas.
- `bigquery/scripts/silver/ddl_silver.sql`: DDL das tabelas novas
  (`ing_yampi_pedidos`, `ing_shopify_clientes_historico`, `log_cargas`) e da coluna
  `ing_ads_diario.dq_cliques_todos_ausente`.
- Limpeza na silver: trim; e-mail/utm_source/utm_medium em minúsculas; vazio vira
  NULL; `cliques_todos < cliques_link` vira NULL com flag; ticket único por `ticket_id`.
- `proc_load_gold.sql`: o gold lê só a silver (`ing_shopify_pedidos`,
  `ing_yampi_pedidos`, `ing_shopify_clientes_historico`).
- `bigquery/tests/quality_checks_silver.sql`: 6 verificações (defasagem, cliques,
  flag, chave duplicada, texto sujo, idade da última carga).

## Validação
- `load_silver()` duas vezes seguidas: 2ª execução com 0 linhas afetadas em todas as tabelas.
- Todas as verificações de silver (6) e gold (11) retornam zero linhas.
- Gold antes/depois (snapshots `*_pre_medalhao`, expiram em 30 dias): receita,
  investimento, pedidos, pago, canal e cliente novo idênticos. Única diferença:
  `utm_source` em minúsculas/NULL em vez de vazio.
- `ing_tickets` tinha 4 tickets duplicados; esvaziada e recarregada uma vez.

## Ajustes posteriores (mesmo dia)
- **`fato_pedidos` com a poddi:** o MERGE insert-only agora também insere os pedidos pagos da
  poddi (121 hoje), classificados por `fato_pedidos_pod`. Snapshot `fato_pedidos_pre_poddi`.
  O gold de 01/11 não mudou (102 pedidos, 68/20/12/2 por canal).
- **Views antigas:** as 18 views de `guara_performance` que liam o bronze passaram a ler a silver;
  `v_todos_dados` deixou de ler `dooki_pedidos_diario` e agrega `ing_yampi_pedidos`. Contagens de
  linhas iguais. `v_dash_overview`/`v_dash_campaigns` idênticas ao antes; `v_ads_*` e
  `v_dashboard_diario` diferem só em `cliques_todos` (NULL quando a fonte não coletou).
- **Tickets/SLA:** o bronze não recebe carga porque o pipeline de transcrição roda em outro host, não nesta máquina.
