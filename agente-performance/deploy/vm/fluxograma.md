# Fluxograma da carga na VM

Caminho de uma execução da carga do BigQuery na VM Oracle, com nova tentativa e alerta de falha.
O mesmo bloco `mermaid` pode ser colado num bloco de código Mermaid do Notion.

```mermaid
flowchart TD
    T["Timer do systemd na VM<br/>0h, 3h, 6h ... 21h de Brasília<br/>Persistent: horário perdido roda quando a VM volta"]
    T --> L{"Outra carga<br/>ainda rodando?"}
    L -- sim --> P["Pula esta execução<br/>(trava flock)"]
    L -- não --> E

    subgraph E["Cada etapa, em ordem"]
        direction TB
        S1["1. Tokens da Shopify<br/>POD, Botica, Source Of"]
        S2["2. Shopify: receita diária e pedidos com UTM<br/>(últimos 8 dias)"]
        S3["3. Google Ads e Meta Ads<br/>(últimos 8 dias)"]
        S4["4. Yampi da POD e histórico de clientes<br/>(últimos 45 dias)"]
        S5["5. Meta semanal<br/>(só às segundas)"]
        S6["6. Reconstrução no BigQuery<br/>bronze → silver → gold<br/>+ 21 verificações de qualidade"]
        S1 --> S2 --> S3 --> S4 --> S5 --> S6
    end

    E --> R{"A etapa falhou?"}
    R -- não --> N["Próxima etapa"]
    R -- sim --> RT["Espera 2 min e tenta de novo<br/>(até 3 tentativas)"]
    RT --> R2{"Ainda falhou<br/>na 3ª tentativa?"}
    R2 -- não --> N
    R2 -- sim --> F["Anota a etapa como falha<br/>e segue para a próxima"]
    F --> N

    N --> FIM{"Alguma etapa<br/>terminou com falha?"}
    FIM -- não --> OK["Registro: logs/cron_master_sync.log<br/>(BigQuery: jobs e alertas_qualidade_dados)"]
    FIM -- sim --> A["Alerta no Slack #alertas-tecnicos<br/>com a lista de etapas<br/>e saída com erro (journalctl)"]

    TO["Carga passou de 90 min"] -.-> K["systemd interrompe a carga"]

    subgraph G["Fora da VM (GitHub Actions, PR 3)"]
        direction TB
        G1["A cada 2h: o gold foi atualizado<br/>nas últimas 4h?"]
        G2["Às 9h: cada cliente tem investimento<br/>até 2 dias atrás?"]
        G1 --> GA["Se não: alerta no Slack"]
        G2 --> GA
    end
```

## O que cada parte cobre

| Falha | Quem percebe | Aviso |
|---|---|---|
| Uma etapa falha uma vez (API instável, timeout) | a própria carga | nenhum: a nova tentativa resolve |
| Uma etapa falha 3 vezes | a própria carga | Slack com a etapa que falhou |
| A carga trava | systemd, em 90 min | saída com erro no `journalctl` (e o alerta de frescor, se o gold parar) |
| A VM cai ou o timer para | ninguém na VM | alerta de frescor do GitHub Actions, em até 4h |
| Uma fonte para de mandar dados, mas a carga roda | alerta de frescor das 9h | Slack com o cliente e o último dia com investimento |
| A verificação de qualidade do BigQuery falha | `rebuild_bq_layers.py` | Slack (etapa "Reconstruir camadas" com falha) e `correlacao.alertas_qualidade_dados` |
