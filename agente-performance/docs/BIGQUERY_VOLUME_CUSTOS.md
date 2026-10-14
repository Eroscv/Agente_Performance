# BigQuery — Volume de Dados e Estimativa de Custos

> **Última atualização:** 2026-10-14
> Dataset: `guara-db.guara_performance` (região US)

---

## 1. Tabelas e Tamanho Atual

| Tabela | Linhas | Tamanho | Período dos dados |
|---|---:|---:|---|
| `ads_diario` | 19.857 | 6,82 MB | 2024-01-23 → hoje |
| `abandoned_checkout_detail` | 949 | 0,33 MB | — |
| `shopify_diario` | 537 | 0,13 MB | — |
| `metricas_sla` | 684 | 0,06 MB | — |
| `abandoned_checkout_daily` | 160 | 0,01 MB | — |
| `meta_campaign_totals` | 5 | < 0,01 MB | em crescimento |
| `analise_reunioes` | 28 | 0,02 MB | — |
| `tickets_alerta` | 29 | 0,01 MB | — |
| `dooki_pedidos_diario` | 3 | < 0,01 MB | — |
| **TOTAL dataset** | **~22.252** | **~7,4 MB** | |

> Views (`v_*`): 11 views analíticas — não armazenam dados, apenas referenciam as tabelas acima.

---

## 2. Detalhamento de `ads_diario` (tabela principal)

| Cliente | Plataforma | Linhas | Período | Investimento | Receita |
|---|---|---:|---|---:|---:|
| source_of | Meta Ads | 10.392 | Jan/2024 → hoje | R$ 1.188.369 | R$ 4.599.762 |
| pod_kombucha | Meta Ads | 7.011 | Jul/2026 → hoje | R$ 385.646 | R$ 1.657.968 |
| pod_kombucha | Google Ads | 1.532 | Ago/2025 → hoje | R$ 188.771 | R$ 1.589.654 |
| source_of | Google Ads | 624 | Mai/2026 → hoje | R$ 59.854 | R$ 567.810 |
| botica | Meta Ads | 298 | Set/2026 → hoje | R$ 2.652 | R$ 313 |

---

## 3. Ingestão Mensal (histórico)

| Mês ingestão | Linhas escritas/atualizadas |
|---|---:|
| Agosto/2026 | 11.610 |
| Setembro/2026 | 2.629 |
| Outubro/2026 | 5.618 |

**Ritmo atual:** ~2.000–6.000 linhas/mês (depende de quantos clientes ativos e retroatividade dos syncs).

---

## 4. Bytes Processados por Query (dry run, sem cache)

Cada consulta ao BQ escaneia a tabela inteira (não há particionamento). Valores medidos via dry run:

| View / Query | MB por execução |
|---|---:|
| `v_ads_diario` | 6,82 MB |
| `v_ads_roas_real` | 6,82 MB |
| `v_mensal` | 1,56 MB |
| `v_alertas_campanha` | 1,97 MB |
| `v_benchmark_mensal` | 1,26 MB |
| `v_diario_90d` | 1,09 MB |
| `v_performance_mensal` | 1,01 MB |
| `v_performance_completa` | 0,99 MB |
| `v_top_campanhas_mes` | 0,28 MB |
| **Média por view** | **~2,4 MB** |
| **Total todas as views (1x cada)** | **~21,8 MB** |

---

## 5. Estimativa de Consumo Mensal

### Consultas do agente (NLP → SQL → BQ)

| Parâmetro | Valor |
|---|---|
| Consultas/dia ao agente | ~15 |
| Views executadas por consulta | ~2,5 |
| MB médio por view | 2,4 MB |
| **Total estimado/dia** | **~90 MB** |
| **Total estimado/mês** | **~2,7 GB** |

### Syncs de ingestão (MERGE via tabela temporária)

Cada sync escaneia a tabela destino para o MERGE + escreve a tmp. Custo de leitura estimado por sync:

| Tabela | MB lido por sync |
|---|---:|
| `ads_diario` (Meta, 30 dias) | ~6,8 MB |
| `ads_diario` (Google, 30 dias) | ~6,8 MB |
| `shopify_diario` | ~0,1 MB |
| `meta_campaign_totals` | < 0,01 MB |

Syncs automáticos (cron diário): ~4–6 syncs/dia → **~40 MB/dia de leitura de ingestão** → ~1,2 GB/mês.

### Resumo

| Origem | GB/mês (estimado) |
|---|---:|
| Consultas do agente | ~2,7 GB |
| Syncs diários | ~1,2 GB |
| **TOTAL** | **~4,0 GB/mês** |

---

## 6. Custo Estimado

### Free tier BigQuery (On-demand)
- **1 TB de queries gratuitas/mês**
- Uso estimado atual: **~4 GB/mês = 0,004 TB**
- Percentual do free tier: **~0,4%**
- **Custo atual: R$ 0,00/mês** ✅

### Quando começa a custar?
Se o volume de dados crescer ou o número de consultas aumentar muito:

| Cenário | Volume/mês | Custo estimado |
|---|---|---|
| Atual | ~4 GB | R$ 0 (free tier) |
| 10× mais consultas | ~40 GB | R$ 0 (free tier) |
| 100× crescimento | ~400 GB | R$ 0 (free tier) |
| Estoura free tier | > 1.000 GB | ~US$ 5/TB excedente |

O dataset atual (7,4 MB) precisaria crescer **~130×** (para ~1 GB) antes de qualquer preocupação com custo de query.

### Storage
- Armazenamento ativo: **~7,4 MB** → US$ 0,02/GB/mês → **< US$ 0,01/mês**
- Após 90 dias sem modificação → long-term storage: US$ 0,01/GB/mês (metade do preço)

---

## 7. Próximos passos para reduzir custo futuro (se necessário)

1. **Particionar `ads_diario` por `data`** — reduz scan para consultas com filtro de período (de 6,8 MB para KB por dia consultado)
2. **Clusterizar por `cliente, plataforma`** — reduz scan em queries filtradas por cliente
3. **BI Engine** — cache in-memory para dashboards de alta frequência (US$ 40/mês por 1 GB de cache)

---

*Gerado automaticamente por análise do dataset `guara-db.guara_performance` em 2026-10-14.*
