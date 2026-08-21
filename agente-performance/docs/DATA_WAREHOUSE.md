# 🗄️ Data Warehouse — Agente de Performance Guará

> Camada analítica local baseada em **DuckDB + Parquet**, coexistindo com o SQLite operacional.

---

## O que é

O DW é a camada de leitura analítica do Agente de Performance. Enquanto o SQLite cuida da escrita operacional (sync de APIs, logs, cache), o DW consolida o histórico em arquivos Parquet e expõe tudo via DuckDB — motor SQL em memória, zero configuração, leitura rápida mesmo com anos de dados.

**Por que foi criado?**
O SQLite funciona bem para operações do dia a dia, mas começa a travar em queries analíticas pesadas (janelas temporais, window functions, joins entre plataformas). O DW resolve isso sem mudar nenhum endpoint existente — as views DuckDB abstraem a fonte de dados para o restante do código.

---

## Arquitetura em 3 camadas

```
┌─────────────────────────────────────────────────────┐
│  APIs externas (Meta Ads · Google Ads · Shopify)    │
└─────────────────────┬───────────────────────────────┘
                      │  sync diário
┌─────────────────────▼───────────────────────────────┐
│  SQLite  (escrita operacional)                      │
│  source_of.db · pod_kombucha.db · shopify_receita   │
│  → dados frescos: hoje e ontem                      │
└─────────────────────┬───────────────────────────────┘
                      │  ETL 07:00 BRT
┌─────────────────────▼───────────────────────────────┐
│  Parquet  (histórico analítico)                     │
│  data/warehouse/fato_performance/*.parquet          │
│  data/warehouse/fato_ads/*.parquet                  │
│  data/warehouse/fato_shopify/*.parquet              │
│  → dados históricos: tudo até antes de ontem        │
└─────────────────────┬───────────────────────────────┘
                      │  leitura unificada
┌─────────────────────▼───────────────────────────────┐
│  DuckDB  (motor analítico)                          │
│  views: v_performance · campanhas_unificada         │
│  → o agente e a FastAPI só enxergam as views        │
└─────────────────────────────────────────────────────┘
```

**Regra simples:** SQLite = escreve. Parquet = armazena histórico. DuckDB = consulta tudo.

---

## Estrutura de arquivos

```
data/
  warehouse/
    dim_cliente.parquet          # clientes cadastrados (~2-10 linhas)
    dim_campanha.parquet         # catálogo de campanhas (~300 linhas, cresce devagar)
    dim_tempo.parquet            # calendário 2024–2030 (2557 dias, gerado uma vez)
    fato_performance/
      fato_performance_2024-01.parquet
      fato_performance_2024-02.parquet
      ...
      fato_performance_2026-08.parquet   # partição do mês atual
    fato_ads/
      fato_ads_2026-07.parquet
      ...
    fato_shopify/
      fato_shopify_2025-06.parquet
      ...
    _etl_state.json              # controle de última execução do ETL

db/
  duck.py                        # conexão DuckDB + registro das views
  etl_warehouse.py               # ETL SQLite → Parquet
  connections.py                 # abstração de conexões (DuckDB + SQLite)
```

---

## Tabelas

### Dimensões

| Tabela | Descrição | Volume |
|---|---|---|
| `dim_cliente` | Cadastro de clientes (IDs, canais Slack, plataformas ativas) | ~2–10 linhas |
| `dim_campanha` | Catálogo de campanhas com surrogate key, objetivo normalizado e datas ativas | ~300 linhas |
| `dim_tempo` | Calendário pré-gerado com ano, mês, semana ISO, dia da semana | 2.557 linhas |

### Fatos

| Tabela | Granularidade | Fonte | Volume atual |
|---|---|---|---|
| `fato_performance` | 1 linha por (data, campanha, plataforma) | Meta Ads + Google Ads agregados | ~36 mil linhas |
| `fato_ads` | 1 linha por (data, anúncio) — **Meta Ads only** | Meta Ads bruto por criativo | ~32 mil linhas |
| `fato_shopify` | 1 linha por (data, cliente) | Shopify receita diária | ~414 linhas |

> **`fato_ads` só existe para Meta Ads.** O Google Ads entrega dados já agregados por campanha na API — não há nível de anúncio disponível.

---

## Views principais (o que o agente usa)

```sql
-- Histórico completo via Parquet
dw_performance        → fato_performance/*.parquet

-- Híbrida: Parquet (histórico) + SQLite (dados frescos de hoje/ontem)
v_performance_full    → dw_performance UNION ALL campanhas_unificada

-- Compatibilidade total com código existente
v_performance         → não muda (código do agente não precisa ser alterado)
campanhas_unificada   → não muda
```

O agente e a FastAPI **não sabem** de onde vêm os dados — leem das mesmas views de sempre.

---

## ETL

O ETL (`db/etl_warehouse.py`) roda automaticamente todo dia às 07:00 BRT via cron.

**O que ele faz:**
1. Lê o SQLite de cada cliente (Meta Ads, Google Ads, Shopify)
2. Agrega para a granularidade correta (nível campanha para `fato_performance`)
3. Calcula métricas derivadas: ROAS, CPA, CTR, CPC médio, taxa de recompra
4. Grava em Parquet particionado por mês (ZSTD — ~5× menor que CSV)
5. Atualiza `_etl_state.json` com status e contagens

**Particionamento mensal:**
Cada arquivo cobre um mês completo. Para reprocessar um período:
```bash
rm data/warehouse/fato_performance/fato_performance_2026-08.parquet
python db/etl_warehouse.py --month 2026-08
```

---

## Por que DuckDB + Parquet?

| Critério | SQLite | DuckDB + Parquet |
|---|---|---|
| Escrita operacional | ✅ Ótimo | ❌ Não recomendado |
| Queries analíticas pesadas | ⚠️ Trava com histórico longo | ✅ Rápido mesmo com milhões de linhas |
| Window functions, ranking | ⚠️ Limitado | ✅ Suporte completo |
| Tamanho em disco | Cresce linearmente | ~5× menor (Parquet + ZSTD) |
| Setup | Zero | Zero (DuckDB é in-process) |
| Backup | Copiar `.db` | Copiar pasta `warehouse/` |

**Não é um banco de dados externo.** DuckDB roda dentro do processo Python, sem servidor, sem porta, sem configuração de rede.

---

## Métricas calculadas no ETL

Calculadas uma vez na gravação — queries de leitura usam direto, sem recalcular:

| Métrica | Fórmula |
|---|---|
| ROAS | `receita / investimento` |
| CPA | `investimento / conversoes` |
| CTR | `cliques / impressoes × 100` |
| CPC médio | `investimento / cliques` |
| Taxa de recompra | `recompradores / pedidos × 100` |

---

## Estado do ETL

O arquivo `data/warehouse/_etl_state.json` registra cada execução:

```json
{
  "ultimo_run": "2026-08-20T07:00:00",
  "ultimo_run_status": "ok",
  "ultimo_dia_processado": "2026-08-19",
  "linhas_por_tabela": {
    "fato_performance": 36194,
    "fato_ads": 32250,
    "fato_shopify": 414,
    "dim_campanha": 298,
    "dim_cliente": 2,
    "dim_tempo": 2557
  },
  "arquivos_parquet": 45,
  "tamanho_total_mb": 12.4
}
```

---

## O que não muda

- Código do agente (`agent/`, `api/`, `relatorio/`) — zero alterações
- Endpoints FastAPI — continuam funcionando igual
- Views DuckDB `v_performance` e `campanhas_unificada` — interface preservada
- SQLite — continua sendo a fonte de escrita operacional

O DW é **somente leitura** do ponto de vista do agente. Nenhum endpoint escreve no Parquet — isso é exclusivo do ETL.

---

## Próximos passos planejados

- **Etapa 4:** Dashboard web (Streamlit ou similar) lendo direto do DW
- **Etapa 5:** Relatório mensal automatizado com dados históricos consolidados via Parquet
- **SCD futuro:** Versionamento de mudanças em `dim_campanha` (nome de campanha, objetivo) se necessário
