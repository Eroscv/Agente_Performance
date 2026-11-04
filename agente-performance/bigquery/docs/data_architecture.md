# Arquitetura Medallion — guara-db

Modelada sobre [DataWithBaraa/sql-data-warehouse-project](https://github.com/DataWithBaraa/sql-data-warehouse-project),
adaptada de SQL Server (CSV + BULK INSERT) para BigQuery (APIs + dual-write
Python, stored procedures nativas do BigQuery).

```mermaid
flowchart LR
    subgraph Fontes["Fontes externas"]
        shopify["Shopify Admin API\n(3 lojas)"]
        meta["Meta Marketing API"]
        gads["Google Ads API"]
        dooki["Dooki/Yampi API\n(POD)"]
    end

    subgraph Bronze["BRONZE — guara_performance\n(raw, 1:1 da fonte)"]
        b1["shopify_diario"]
        b2["pedidos_diario"]
        b3["ads_diario"]
        b4["abandoned_checkout_daily /\nabandoned_checkout_detail"]
        b5["dooki_pedidos_diario"]
    end

    subgraph Silver["SILVER — ingestao\n(ing_&lt;entidade&gt;, limpo/padronizado)"]
        s1["ing_shopify_resumo_diario"]
        s2["ing_shopify_pedidos"]
        s3["ing_ads_diario\n(+ nivel)"]
        s4["ing_checkout_abandonado_diario /\ning_checkout_abandonado_detalhe"]
    end

    subgraph Gold["GOLD — correlacao\n(dim_/fato_, pronto pro negócio)"]
        g1["dim_cliente /\ndim_cliente_alias"]
        g2["fato_pedidos"]
        g3["fato_performance_diaria"]
    end

    subgraph Consumo["Consumo"]
        dash["dashboard-source-of/\n(GitHub Pages)"]
        notion["Notion / relatórios"]
    end

    shopify --> b1 & b2 & b4
    meta --> b3
    gads --> b3
    dooki --> b5

    b1 --> s1
    b2 --> s2
    b3 --> s3
    b4 --> s4

    s2 -. "+ canal, MERGE incremental" .-> g2
    s3 --> g3
    g2 --> g3
    b5 -. "Yampi/Dooki (POD)" .-> g3
    g1 -.-> g2 & g3

    g2 & g3 --> dash
    g2 & g3 --> notion
```

## As três camadas

**Bronze (`guara_performance`)** — espelho bruto de cada fonte, sem
transformação. Gravado em dual-write pelos scripts de sync
(`cron_shopify_sync.py`, `sync_pedidos_diario.py`, `sync_gads_diario.py`,
`sync_meta_diario.py`) via `db/bq_writer.py`, rodando a cada 6h pelo
`scripts/cron_master_sync.sh`. Serve como trilha de auditoria: se um
cálculo do gold parecer errado, o primeiro lugar a conferir é aqui.

**Silver (`ingestao`)** — mesmas entidades do bronze (`ing_<entidade>`),
com limpeza mínima (nesta base: nenhuma mudança de schema, só
rematerialização + a coluna `nivel` derivada em `ing_ads_diario`).
Reconstruída pela stored procedure `ingestao.load_silver()`
(`bigquery/scripts/silver/proc_load_silver.sql`) — full refresh de cada
tabela via `CREATE OR REPLACE TABLE ... AS SELECT`, já que as fontes
bronze são pequenas o suficiente para reprocessar por completo a cada
chamada.

**Gold (`correlacao`)** — fatos e dimensões prontos para consumo:
`dim_cliente`/`dim_cliente_alias` (cadastro estático de clientes e seus
aliases de UTM/source), `fato_pedidos` (pedidos pagos + canal derivado,
MERGE incremental — nunca sobrescreve histórico), `fato_performance_diaria`
(investimento + receita + ROAS por cliente/canal/dia, full rebuild).
Reconstruída pela stored procedure `correlacao.load_gold()`
(`bigquery/scripts/gold/proc_load_gold.sql`).

`scripts/rebuild_bq_layers.py` só dispara as duas procedures, nessa ordem
(silver antes de gold, porque `fato_performance_diaria` lê
`ingestao.ing_ads_diario`). É o último passo de `scripts/cron_master_sync.sh`.

## Fora do escopo desta camada

`ingestao.ing_tickets`, `ingestao.ing_metricas_sla`,
`correlacao.fato_tickets`, `correlacao.alertas_qualidade_dados` e
`correlacao.qualidade_dados_checks` pertencem a um subsistema diferente
(SLA de tickets de suporte, usado pelo agente de transcrição —
`MetricasAgent`/`TicketAgent`), não ao pipeline de performance de mídia
paga. Não são tocados por `load_silver`/`load_gold`.
