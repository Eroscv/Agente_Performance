# ⚙️ Agente de Performance Guará

> Analista de mídia paga com IA — Meta Ads + Google Ads + Shopify — para os clientes da **Guará Marketing**.

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)](https://fastapi.tiangolo.com)
[![Claude Haiku 4.5](https://img.shields.io/badge/Claude-Haiku_4.5-orange.svg)](https://anthropic.com)
[![Slack Bolt](https://img.shields.io/badge/Slack-Bolt-blueviolet.svg)](https://slack.dev/bolt-python)
[![BigQuery](https://img.shields.io/badge/BigQuery-guara--db-4285F4.svg)](https://cloud.google.com/bigquery)

---

## 📌 O que é este projeto

O **Agente de Performance Guará** é um sistema de análise de campanhas publicitárias acionado por linguagem natural via Slack. Roda como processo único (`main.py`) em uma VPS Ubuntu (`44.204.251.251`, porta `8001`), gerenciado pelo systemd com restart automático.

### Clientes ativos

| Cliente | Meta Ads | Google Ads | Shopify | Dooki |
|---|---|---|---|---|
| **Source OF** | ✅ `act_1059884611898142` | ✅ `4311260590` | ✅ | — |
| **POD Kombucha** | ✅ | ✅ MCC `612-367-2195` | ✅ | ✅ |
| **Botica Ouro Preto** | ✅ | — | ✅ | — |

### Stack

| Camada | Tecnologia |
|---|---|
| API / servidor | FastAPI 0.111 + Uvicorn |
| Interface | Slack Bolt (AsyncApp) |
| IA | Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) |
| Banco operacional | SQLite (múltiplos arquivos por cliente) |
| Motor analítico local | DuckDB (views federadas sobre SQLite) |
| Espelho analítico | Google BigQuery (`guara-db.guara_performance`) |
| Relatórios | Notion API (Block Kit) + Slack threads |
| Infraestrutura | VPS Ubuntu + Apache 2.4.58 + systemd |

---

## 1️⃣ O que foi feito

### Visão geral funcional

O sistema faz três coisas em paralelo:

**1. Coleta contínua de dados**
A cada 15 minutos, crons independentes sincronizam dados das APIs de Meta Ads, Google Ads e Shopify para o SQLite local. Cada plataforma tem seu próprio cron com offset para não colidir:

```
:00/:15/:30/:45  → meta-sync-horario    (Meta Ads    → SQLite + BigQuery)
:05/:20/:35/:50  → gads-sync-horario    (Google Ads  → SQLite + BigQuery)
:10/:25/:40/:55  → shopify-sync-horario (Shopify     → SQLite + BigQuery)
:02/:17/:32/:47  → dashboard-ingest-15min (ingestão incremental dashboard)
06:00 UTC diário → shopify-sync-diario  (Shopify histórico: últimos 8 dias)
```

**2. Resposta a perguntas em português (Slack)**
Qualquer mensagem que menciona o bot no Slack dispara o pipeline conversacional: extração de intenção → consulta SQL no SQLite → formatação com Claude → resposta na thread.

**3. Relatórios automáticos**
Relatórios diários, semanais e mensais são gerados automaticamente e publicados no Notion com link enviado no Slack.

### Arquitetura de dados: dual-write

Todo dado percorre dois destinos em paralelo:

```
API Externa
    │
    ▼
loader.py ──► SQLite (escrita síncrona — fonte primária)
    │
    └──► bq_writer.py ──► BigQuery (escrita assíncrona — espelho analítico)
              │                    falha silenciosa: nunca bloqueia o sync principal
              │
              ▼
         MERGE (upsert)
         via tabela temporária
```

O SQLite é a fonte da verdade. O BigQuery é usado para dashboards (Looker Studio), queries ad-hoc e análises cross-cliente que seriam lentas em SQLite.


---

## 2️⃣ Como foi feito

### Estrutura de diretórios

```
agente-performance/
├── main.py                      # Entry point: FastAPI app + Slack Bolt handler
├── clientes.json                # Registry de clientes — adicionar cliente = adicionar bloco aqui
├── requirements.txt
├── .env                         # Credenciais (nunca no git)
│
├── agent/                       # Núcleo do agente conversacional
│   ├── engine.py                # Query engine — 20+ tipos de consulta SQL
│   ├── nlp.py                   # Extração de intenção + entidades (datas, clientes, métricas)
│   ├── orchestrator_agent.py    # Roteia a pergunta para o agente do cliente correto
│   ├── claude_client.py         # Formata resposta final via Claude Haiku
│   ├── cache.py                 # Cache L1 (RAM) + L2 (SQLite), TTL configurável
│   ├── context_store.py         # Memória de conversa por thread Slack (TTL 24h)
│   ├── comparatives.py          # Comparativos WoW, MoM, períodos, rankings
│   ├── sql_generator.py         # Text-to-SQL fallback para perguntas fora do template
│   ├── confidence.py            # Nível de confiança + citação de fonte dos dados
│   ├── queries/                 # Templates SQL por tipo de intenção
│   └── clients/                 # Implementações por cliente (source_of, pod_kombucha, botica)
│
├── api/                         # Endpoints REST FastAPI
│
├── db/                          # Camada de dados
│   ├── bq_writer.py             # Dual-write BigQuery: MERGE via tabela temporária
│   ├── shopify_sync.py          # Sync Shopify → SQLite
│   ├── connection.py            # Gerenciador de conexão SQLite (singleton por arquivo)
│   ├── duck.py                  # DuckDB: views federadas sobre múltiplos SQLite
│   ├── schema.py                # DDL: criação de todas as tabelas
│   ├── validator.py             # Validação de integridade pós-ingestão
│   ├── sync_alerts.py           # Alertas Slack quando sync falha 2x seguidas
│   └── cron_log.py              # Log de execuções de cron
│
├── meta_ads/
│   ├── client.py                # Autenticação Meta Business SDK
│   └── loader.py                # Ingestão Meta Ads → SQLite (+ dual-write BQ)
│
├── google_ads/
│   ├── client.py                # Autenticação Google Ads API (OAuth2)
│   └── loader.py                # Ingestão Google Ads → SQLite (+ dual-write BQ)
│
├── shopify/                     # Integração Shopify (Source OF + Botica)
├── dooki/                       # Integração Dooki (POD Kombucha)
│
├── relatorio/                   # Sistema de relatórios
│   ├── builder_semanal.py       # Relatório semanal (análise WoW, metas, narrativa IA)
│   ├── builder_mensal.py        # Relatório mensal (17 seções, fechamento completo)
│   ├── builder_diario.py        # Relatório diário (resumo + comparativo D-1)
│   ├── builder_periodo.py       # Período customizado (data_inicio / data_fim)
│   ├── builder_pod.py           # Relatório especializado POD Kombucha
│   ├── builder_shared.py        # Componentes reutilizáveis Notion Block Kit
│   └── regras.py                # Regras de negócio da Guará (ROAS mínimo, fatores, SLA)
│
├── notion/                      # Publicação de relatórios no Notion
├── slack/                       # Handlers de eventos Slack Bolt
├── scripts/                     # Scripts utilitários e crons
├── docs/                        # Documentação técnica detalhada
├── alteracoes/                  # Log de deploys (YYYY-MM-DD_slug.md por alteração)
└── data/                        # SQLite databases — NÃO vai ao repositório
    ├── agente.db                # Banco principal
    ├── context.db               # Contexto de conversas (TTL 24h)
    ├── cache.db                 # Cache de respostas
    └── dashboard_sync.db        # Estado do sync (last_sync por cliente/plataforma)
```

### Pipeline de uma pergunta no Slack

```
Usuário digita "@agente como foi o ROAS essa semana?"
        │
        ▼
FastAPI /slack/events  →  ack() imediato (Slack exige resposta em < 3s)
        │
        ▼ (assíncrono, pode demorar)
agent/nlp.py
  → intent: ROAS_PERIODO
  → cliente: source_of (padrão se não mencionado)
  → data_inicio: segunda-feira da semana atual
  → data_fim: hoje
        │
        ▼
agent/engine.py
  → seleciona query template para ROAS_PERIODO
  → executa SQL no SQLite (DuckDB ou direto)
  → retorna DataFrame com resultados
        │
        ▼
agent/claude_client.py
  → monta prompt: "Dados: {DataFrame}. Formate em tabela Slack Block Kit."
  → chama Claude Haiku 4.5 (modelo econômico, resposta rápida)
  → retorna blocos Slack formatados
        │
        ▼
say() → resposta na thread do Slack
```

### Banco de dados: tabelas SQLite por cliente

Padrão de nomenclatura: `{cliente_key}_{plataforma}_diario`

| Tabela | Conteúdo | Chave única |
|---|---|---|
| `source_of_meta_ads_diario` | Campanhas Meta Ads — Source OF | `(data, campaign_id, adset_id, ad_id)` |
| `source_of_gads_diario` | Campanhas Google Ads — Source OF | `(data, campaign_id)` |
| `pod_kombucha_meta_ads_diario` | Meta Ads — POD Kombucha | `(data, campaign_id, adset_id, ad_id)` |
| `pod_kombucha_gads_diario` | Google Ads — POD Kombucha | `(data, campaign_id)` |
| `shopify_receita_diaria` | Receita diária Shopify (todos os clientes) | `(cliente, data)` |
| `dooki_pedidos_diario` | Pedidos Dooki — POD Kombucha | `(data, pedido_id)` |

**DuckDB como camada de federação:**
O módulo `db/duck.py` cria uma conexão DuckDB que enxerga todos os SQLite como fontes unificadas via views:

```python
# db/duck.py — simplificado
conn = duckdb.connect(":memory:")
conn.execute("""
    ATTACH 'data/agente.db' AS agente (TYPE sqlite);
    CREATE VIEW v_performance AS
        SELECT 'source_of' as cliente, 'meta' as plataforma, *
        FROM agente.source_of_meta_ads_diario
    UNION ALL
        SELECT 'source_of', 'google', *
        FROM agente.source_of_gads_diario
    -- ... demais clientes
""")
```


### BigQuery: schema e dual-write

**Projeto GCP:** `guara-db` | **Dataset:** `guara_performance`

#### Tabela `ads_diario`

Consolidada — Meta Ads + Google Ads de todos os clientes em uma única tabela particionada.

```sql
CREATE TABLE `guara-db.guara_performance.ads_diario` (
  cliente             STRING,    -- 'source_of' | 'pod_kombucha' | 'botica'
  plataforma          STRING,    -- 'meta' | 'google'
  data                DATE,      -- partição principal
  campanha            STRING,
  campaign_id         STRING,
  adset_name          STRING,    -- NULL para Google Ads
  adset_id            STRING,    -- NULL para Google Ads
  ad_name             STRING,    -- NULL para Google Ads
  ad_id               STRING,    -- NULL para Google Ads
  objetivo            STRING,
  status              STRING,
  investimento        NUMERIC,
  compras             INT64,
  receita             NUMERIC,
  impressoes          FLOAT64,
  alcance             FLOAT64,
  cliques_link        FLOAT64,
  cliques_todos       FLOAT64,
  roas                FLOAT64,
  cpa                 FLOAT64,
  cpm                 FLOAT64,
  frequencia          FLOAT64,
  -- campos exclusivos Meta Ads:
  video_3s            FLOAT64,
  video_50pct         FLOAT64,
  thruplays           FLOAT64,
  adicoes_carrinho    FLOAT64,
  checkout_iniciado   FLOAT64,
  visualizacoes_landing FLOAT64,
  qualidade_ranking   STRING,
  engagement_ranking  STRING,
  conversion_ranking  STRING,
  criado_em           TIMESTAMP  -- timestamp de inserção, usado como tiebreaker no MERGE
)
PARTITION BY data          -- lê apenas a partição do dia filtrado
CLUSTER BY cliente, plataforma;  -- acelera filtros por cliente/plataforma
```

#### Mecanismo de MERGE (upsert)

O `bq_writer.py` nunca faz INSERT direto. Usa sempre um ciclo de 3 passos:

```python
# db/bq_writer.py — _insert() simplificado
def _insert(table_id, rows, desc):
    # 1. Cria tabela temporária com TTL de 1 dia
    tmp = f"{project}.{dataset}._{table_id}_tmp_{timestamp}"
    bq.create_table(Table(tmp, schema=schema), exists_ok=True)

    # 2. Insere dados novos na temp
    bq.insert_rows_json(bq.get_table(tmp), rows)

    # 3. MERGE: atualiza se existe, insere se não existe
    bq.query(f"""
        MERGE `{full_table}` T
        USING `{tmp}` S
        ON {on_clause}
        WHEN MATCHED AND S.criado_em > T.criado_em THEN
            UPDATE SET {update_clause}
        WHEN NOT MATCHED THEN
            INSERT ({insert_cols}) VALUES ({insert_vals})
    """).result()

    # 4. Limpa a temp
    bq.delete_table(tmp, not_found_ok=True)
```

**Chaves de MERGE dinâmicas por plataforma:**

```python
if table_id == "ads_diario":
    plataforma = rows[0].get("plataforma", "")
    if plataforma == "google":
        # Google Ads não tem ad_id/adset_id — chave só até campaign
        keys = ["cliente", "plataforma", "data", "campaign_id"]
    else:
        # Meta Ads tem granularidade até o anúncio individual
        keys = ["cliente", "plataforma", "data", "campaign_id", "adset_id", "ad_id"]
```

**Por que `IS NOT DISTINCT FROM` no ON clause:**

```sql
-- ❌ ERRADO: NULL = NULL → FALSE → MERGE sempre insere, nunca atualiza
ON T.ad_id = S.ad_id

-- ✅ CORRETO: NULL IS NOT DISTINCT FROM NULL → TRUE → MERGE faz match
ON (T.ad_id IS NOT DISTINCT FROM S.ad_id)
```

Isso é crítico para o Google Ads, onde `ad_id` e `adset_id` chegam como `NULL`.

#### Views analíticas

| View | O que calcula |
|---|---|
| `v_performance_mensal` | ROAS real, CPA, variação MoM/YoY cruzando ads + Shopify |
| `v_ads_diario` | Última versão de cada anúncio por dia (deduplicação de segurança via ROW_NUMBER) |
| `v_benchmark_mensal` | Benchmarks históricos por cliente/plataforma |
| `v_top_campanhas_mes` | Top campanhas por ROAS no mês corrente |
| `v_alertas_campanha` | Campanhas abaixo do ROAS mínimo nos últimos 3 dias |
| `v_diario_90d` | Série temporal 90 dias para tendências |

### Sistema de relatórios

Todos os builders recebem `publicar=True/False` — quando `False`, geram os blocos sem efeitos externos (útil para testes):

```python
# Testar sem publicar no Notion
from relatorio.builder_semanal import gerar_relatorio_semanal
blocos = gerar_relatorio_semanal("source_of", publicar=False)
print(f"{len(blocos)} blocos Notion gerados")
```

**Fluxo de publicação:**

```
builder_*.py
  → gera lista de blocos Notion Block Kit
  → notion/historico.py → cria/atualiza página no Notion
  → slack/handlers.py   → posta link + resumo no Slack como thread
```

### Regras de negócio (`relatorio/regras.py`)

```python
ROAS_MINIMO_ACEITAVEL = 1.5          # abaixo disso → alerta
DIAS_SEM_CONVERSAO_ALERTA = 3        # sem compra por N dias → alerta
FATOR_IMPOSTO_META = 1.1215          # impostos sobre investimento em mídia
NO_CONVERSION_DAYS = 3               # configurável via .env

# ROAS real = Receita Shopify ÷ (Investimento Meta × 1.1215)
# Usa receita Shopify (não atribuição Meta) como numerador
# Usa investimento com imposto como denominador
```


---

## 3️⃣ Por que foi feito desse jeito

### Por que SQLite como banco primário (e não Postgres ou o próprio BigQuery)?

**SQLite:**
- Zero custo de infraestrutura — roda no mesmo processo Python
- Latência de leitura em microssegundos (arquivo local em disco)
- Sem conexão TCP, sem pool de conexões, sem timeout de rede
- Adequado para o volume atual: dezenas de milhares de linhas, não bilhões
- Restart do processo não perde nada — tudo está em disco

**Por que não Postgres/Supabase:**

| Critério | SQLite | Postgres/Supabase |
|---|---|---|
| Custo mensal | R$ 0 | ~R$ 130/mês (instância mínima) |
| Latência | ~0.1ms (local) | ~5-20ms (TCP + rede) |
| Manutenção | Zero | Backup, patch, uptime |
| Escala atual | Suficiente | Over-engineered |
| Migrations | `schema.py` simples | Alembic/flyway necessários |

**Por que não usar só o BigQuery:**
O BQ não é adequado para escrita transacional frequente. `MERGE` processa dados por TB — 96 merges/dia de ~500KB cada cobrariam por bytes processados e teriam latência de 2-5 segundos por operação. O SQLite responde em < 1ms.

### Por que dual-write em vez de ETL noturno?

**ETL noturno** sincronizaria uma vez por dia às 23h. Latência máxima: 23h59min. Dashboards do Looker Studio estariam sempre desatualizados durante o dia.

**Dual-write** mantém o BQ atualizado em ≤ 15 minutos, mesmo tempo de defasagem dos dados no SQLite (as próprias APIs de Meta/Google atualizam retroativamente dentro desse intervalo). Dashboards sempre mostram dados do dia atual.

O custo da complexidade extra (bq_writer.py, tabelas temporárias, lógica de MERGE) é compensado pela utilidade prática para análises em tempo real.

### Por que MERGE com tabela temporária (e não streaming insert)?

O BigQuery tem dois modos de escrita:

1. **Streaming insert** (`insert_rows_json`) — append puro, sem deduplicação, dados ficam no "streaming buffer" por ~90 minutos antes de serem disponíveis para DML
2. **Load job + MERGE** — batch, suporta deduplicação, dados disponíveis imediatamente para queries

O streaming insert foi o que causou **192x duplicação** em 09/10/2026 — cada um dos 96 syncs diários adicionava linhas novas sem verificar se já existiam. O MERGE resolve isso estruturalmente.

### Por que uma tabela `ads_diario` consolidada (Meta + Google) em vez de tabelas separadas?

**Tabela única:**
- Uma query para comparar Meta vs Google (sem JOIN cross-table)
- Schema de views mais simples
- Looker Studio: uma fonte de dados, sem blends manuais
- Particionamento por `data` funciona para ambas as plataformas

**Desvantagem:** campos exclusivos de Meta (`video_3s`, `thruplays`, etc.) ficam `NULL` para linhas do Google. Aceitável — é explícito no schema.

### Por que Claude Haiku 4.5 e não Sonnet ou GPT-4?

| Modelo | Custo por 1M tokens input | Latência típica | Adequação |
|---|---|---|---|
| Claude Haiku 4.5 | ~$0.08 | ~0.5s | ✅ Formatação de respostas |
| Claude Sonnet 4.5 | ~$3.00 | ~1.5s | ✅ Análise complexa (relatórios) |
| GPT-4o | ~$2.50 | ~1.5s | ✅ mas sem vantagem sobre Sonnet |

O agente usa Haiku para respostas rápidas no Slack (objetivo: < 2s) e Sonnet para builders de relatório onde qualidade narrativa importa mais que velocidade.

### Por que particionamento por `data` e clustering por `cliente, plataforma`?

Sem particionamento, toda query varreria a tabela inteira:
```sql
-- SEM partição: varre todas as ~22.700 linhas, processa tudo
SELECT SUM(investimento) FROM ads_diario WHERE data = '2026-10-13';

-- COM partição por data: lê apenas a partição de 2026-10-13 (~180 linhas)
-- Custo BQ proporcional ao tamanho da partição, não da tabela
```

Com clustering por `(cliente, plataforma)`, queries filtradas por cliente dentro de uma partição leem apenas o bloco relevante do arquivo — relevante à medida que a tabela cresce.

### Por que DuckDB para federação local (e não queries diretas em cada SQLite)?

Com múltiplos SQLites por cliente, uma query cross-cliente precisaria de múltiplas conexões e um UNION manual em Python. DuckDB resolve isso com uma view unificada:

```python
# SEM DuckDB: precisa iterar por cliente em Python
resultados = []
for cliente in ["source_of", "pod_kombucha"]:
    conn = sqlite3.connect(f"data/{cliente}.db")
    resultados += conn.execute("SELECT ...").fetchall()

# COM DuckDB: query única, join entre SQLites diferentes
duck.execute("SELECT * FROM v_performance WHERE data >= '2026-10-01'")
```

DuckDB também suporta window functions eficientes (LAG, ROW_NUMBER, RANK) que o SQLite suporta mas de forma mais lenta em datasets maiores.


---

## 4️⃣ Consequências: custos, crescimento e monitoramento

### Custo atual (outubro/2026)

| Recurso | Usado | Limite gratuito | % utilizado | Custo |
|---|---|---|---|---|
| BQ Storage | 7.96 MB | 10 GB/mês | **0.08%** | R$ 0,00 |
| BQ Queries | ~1.4 GB/mês (96 MERGEs/dia × ~500KB) | 1 TB/mês | **0.14%** | R$ 0,00 |
| VPS (servidor) | — | — | — | custo fixo existente |
| Claude Haiku | ~50-200 req/dia × $0.08/1M tokens | — | mínimo | ~R$ 2-5/mês |
| Meta Ads API | gratuita | — | — | R$ 0,00 |
| Google Ads API | gratuita | — | — | R$ 0,00 |
| Shopify API | gratuita | — | — | R$ 0,00 |

**Total mensal incremental: ~R$ 2-5** (só o Claude)

### Projeções de crescimento — BigQuery

Cada cliente ativo gera ~150-200 linhas/dia em `ads_diario` (campanhas Meta + Google):

| Cenário | Clientes | Anos | Linhas estimadas | Tamanho | Custo BQ |
|---|---|---|---|---|---|
| Hoje | 3 | 0 | 22.700 | 7.8 MB | **R$ 0** |
| Curto prazo | 3 | 1 | ~95.000 | ~33 MB | **R$ 0** |
| Médio prazo | 5 | 2 | ~365.000 | ~127 MB | **R$ 0** |
| Longo prazo | 10 | 5 | ~3.650.000 | ~1.27 GB | **R$ 0** |
| Limite gratuito | — | — | — | 10 GB | **R$ 0** |
| Acima do free tier | — | — | — | > 10 GB | ~R$ 0,10/GB/mês |

**Conclusão:** com o crescimento esperado da Guará, o BigQuery não vai custar nada por anos. O único risco de custo é **queries sem filtro de data** (full table scan). Com particionamento ativo, isso está mitigado estruturalmente.

### Projeções de custo — queries BQ

Cenário de risco: dashboard Looker Studio com query sem `WHERE data >=` rodando a cada atualização de página:

```sql
-- ⚠️ PERIGOSO: varre toda a tabela sem partição
SELECT cliente, SUM(investimento)
FROM `guara-db.guara_performance.ads_diario`
GROUP BY cliente;

-- ✅ SEGURO: usa a partição
SELECT cliente, SUM(investimento)
FROM `guara-db.guara_performance.ads_diario`
WHERE data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY cliente;
```

Com 1 GB de dados e 100 queries sem filtro/mês: 100 GB processados = ~R$ 3/mês (BQ cobra $5/TB = ~R$ 28/TB).

### O que monitorar e com que frequência

#### 🔴 Crítico — verificar semanalmente (toda segunda-feira)

**1. Duplicatas em `ads_diario`**

A tabela já sofreu dois episódios de duplicação (09/10 e 13/10/2026). Verificar ratio linhas/combos únicos — deve ser sempre 1.0:

```sql
-- Cole direto no BigQuery Console
SELECT
  data,
  COUNT(*) AS total_linhas,
  COUNT(DISTINCT CONCAT(
    cliente, '_', plataforma, '_',
    COALESCE(campaign_id, '_'), '_',
    COALESCE(adset_id, '_'), '_',
    COALESCE(ad_id, '_')
  )) AS combos_unicos,
  ROUND(COUNT(*) / COUNT(DISTINCT CONCAT(
    cliente, '_', plataforma, '_',
    COALESCE(campaign_id, '_'), '_',
    COALESCE(adset_id, '_'), '_',
    COALESCE(ad_id, '_')
  )), 1) AS ratio
FROM `guara-db.guara_performance.ads_diario`
WHERE data >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 7 DAY)
GROUP BY data
ORDER BY data DESC;
```

**Resultado esperado:** todas as linhas com `ratio = 1.0`. Qualquer `ratio > 1.2` indica duplicação ativa — executar o procedimento de limpeza em `alteracoes/2026-10-13_bigquery-dedup-fix-merge-chave-plataforma.md`.

**2. Log do agente — erros BQ**

```bash
journalctl -u agente-guara.service --since "7 days ago" | grep "\[BQ\]" | grep -v "✅"
```

Saída esperada: silêncio (ou só linhas `✅ MERGE`). Qualquer `falha silenciosa` indica que o dual-write está quebrado e o BQ está desatualizado.

#### 🟡 Importante — verificar mensalmente (primeiro dia do mês)

**3. Tabelas temporárias órfãs**

O MERGE cria `_ads_diario_tmp_YYYYMMDD...` e deleta no final. Se o processo travar no meio, a tabela fica acumulando (cada uma tem TTL de 1 dia, mas é melhor verificar):

```bash
# Via GCP Console > BigQuery > guara_performance
# Filtrar por tables com prefixo "_" com data de criação > 2 dias atrás
```

Ou via Python:
```python
from google.cloud import bigquery
client = bigquery.Client(project="guara-db")
for t in client.list_tables("guara_performance"):
    if t.table_id.startswith("_"):
        print(t.table_id, t.created)
```

**4. Crescimento do dataset**

```sql
SELECT table_id,
  FORMAT('%\',d', row_count) AS linhas,
  ROUND(size_bytes / 1024 / 1024, 1) AS mb
FROM `guara-db.guara_performance.__TABLES__`
ORDER BY size_bytes DESC;
```

Crescimento saudável em `ads_diario`: ~150-200 linhas/dia por cliente. Pico acima de 500 linhas/dia sugere sync duplicado (verificar ratio no item 1).

**5. Verificar se o dual-write está ativo**

```bash
cd /home/ubuntu/.openclaw/workspace/agente-performance
/home/ubuntu/.openclaw/workspace/agente-performance/venv/bin/python3 -c "
import sys, os; sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from db.bq_writer import enabled
print('BQ dual-write ativo:', enabled())
"
```

Se retornar `False`: verificar se `credentials/bq-service-account.json` existe e se `GOOGLE_APPLICATION_CREDENTIALS` está apontando para ele no `.env`.

#### 🟢 Rotina — sempre que fizer deploy

**6. Verificar status do serviço**

```bash
systemctl status agente-guara.service
journalctl -u agente-guara.service --since "1 hour ago" | tail -50
```

**7. Checar crons ativos**

```bash
openclaw cron list
```

Crons esperados (todos `ok`):
```
meta-sync-horario        cron 0,15,30,45 * * * *   → ok
gads-sync-horario        cron 5,20,35,50 * * * *   → ok
shopify-sync-horario     cron 10,25,40,55 * * * *  → ok
dashboard-ingest-15min   cron 2,17,32,47 * * * *   → ok
shopify-sync-diario      cron 0 6 * * *            → ok
```

**8. Verificar saúde do banco SQLite**

```bash
cd /home/ubuntu/.openclaw/workspace/agente-performance
sqlite3 data/agente.db "PRAGMA integrity_check; SELECT COUNT(*) FROM source_of_meta_ads_diario;"
```


---

## 🔧 Como rodar

### Pré-requisitos

- Python 3.12+
- Acesso às APIs: Meta Ads, Google Ads, Shopify, Notion, Slack, Anthropic
- Service account GCP com permissão `BigQuery Data Editor` no dataset `guara_performance`

### Instalação

```bash
git clone https://github.com/Eroscv/Agente_Performance.git
cd agente-performance
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Editar .env com as credenciais
```

### Variáveis de ambiente obrigatórias

```env
# IA
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-haiku-4-5-20251001

# Slack
SLACK_SIGNING_SECRET=...
SLACK_BOT_TOKEN=xoxb-...

# Notion
NOTION_API_KEY=secret_...
NOTION_DATABASE_ID=...

# Meta Ads
META_APP_ID=...
META_APP_SECRET=...
META_ACCESS_TOKEN=...

# Google Ads
GOOGLE_ADS_DEVELOPER_TOKEN=...
GOOGLE_ADS_CLIENT_ID=...
GOOGLE_ADS_CLIENT_SECRET=...
GOOGLE_ADS_REFRESH_TOKEN=...
GOOGLE_ADS_LOGIN_CUSTOMER_ID=...

# Shopify (Source OF)
SHOPIFY_STORE=source-of.myshopify.com
SHOPIFY_TOKEN=shpat_...

# BigQuery
GOOGLE_APPLICATION_CREDENTIALS=/caminho/para/bq-service-account.json
BQ_PROJECT=guara-db
BQ_DATASET=guara_performance

# App
PORT=8001
DB_PATH=data/agente.db
LOG_LEVEL=INFO
ROAS_ALERT_THRESHOLD=1.5
NO_CONVERSION_DAYS=3
```

### Rodando o servidor

```bash
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

### Verificar saúde

```bash
# Status do serviço (produção)
systemctl status agente-guara.service

# Health check da API
curl http://localhost:8001/health

# BigQuery ativo?
python3 -c "
import sys; sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from db.bq_writer import enabled
print('BQ:', 'ativo' if enabled() else 'INATIVO')
"
```

---

## 📊 Queries úteis no BigQuery

```sql
-- Investimento e ROAS dos últimos 30 dias — Source OF
SELECT
  data,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2) AS investimento,
  SUM(compras) AS compras,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2) AS receita,
  ROUND(SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)), 0), 2) AS roas
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 30 DAY)
GROUP BY data
ORDER BY data DESC;

-- Comparativo Meta vs Google — semana atual
SELECT
  plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2) AS investimento,
  SUM(compras) AS compras,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2) AS receita,
  ROUND(SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)), 0), 2) AS roas
FROM `guara-db.guara_performance.ads_diario`
WHERE data >= DATE_TRUNC(CURRENT_DATE('America/Sao_Paulo'), WEEK(MONDAY))
GROUP BY plataforma;

-- Receita Shopify mensal — Source OF
SELECT
  FORMAT_DATE('%Y-%m', data) AS mes,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2) AS receita,
  SUM(pedidos) AS pedidos,
  SUM(novos_clientes) AS novos,
  SUM(recompradores) AS recompradores,
  ROUND(SUM(CAST(receita AS FLOAT64)) / NULLIF(SUM(pedidos), 0), 2) AS ticket_medio
FROM `guara-db.guara_performance.shopify_diario`
WHERE cliente = 'source_of'
GROUP BY mes
ORDER BY mes DESC;

-- Top 10 campanhas por ROAS — últimos 7 dias
SELECT
  cliente, campanha, plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2) AS investimento,
  SUM(compras) AS compras,
  ROUND(SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)), 0), 2) AS roas
FROM `guara-db.guara_performance.ads_diario`
WHERE data >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 7 DAY)
  AND CAST(investimento AS FLOAT64) > 0
GROUP BY cliente, campanha, plataforma
ORDER BY roas DESC
LIMIT 10;
```

---

## 🗂️ Histórico de incidentes

| Data | Problema | Causa raiz | Fix |
|---|---|---|---|
| 09/10/2026 | 192x duplicação em `ads_diario` | `insert_rows_json` sem upsert — 96 syncs/dia × append puro | MERGE via tabela temporária + view `v_ads_diario` de segurança |
| 13/10/2026 | 8–12x duplicação persistia | Chave MERGE usava `ad_id` (NULL no Google Ads). `NULL = NULL → FALSE` no SQL | Chave dinâmica por plataforma + `IS NOT DISTINCT FROM` no ON clause |

Para detalhes completos: `alteracoes/2026-10-09_bigquery-deduplicacao.md` e `alteracoes/2026-10-13_bigquery-dedup-fix-merge-chave-plataforma.md`.

---

## 🔒 Segurança

- `.env` e `credentials/bq-service-account.json` **nunca** vão ao repositório (`.gitignore` configurado)
- Tokens de API armazenados exclusivamente em variáveis de ambiente
- SQLite local — sem porta exposta externamente
- API FastAPI exposta apenas via proxy Apache (sem SSL direto no app)
- Service account GCP com permissão mínima: somente `BigQuery Data Editor` no dataset específico

---

## 📁 Documentação adicional

| Arquivo | Conteúdo |
|---|---|
| `docs/BIGQUERY.md` | Configuração detalhada BigQuery, troubleshooting, queries |
| `docs/ARQUITETURA_DADOS.md` | Arquitetura dual-write, decisões de design, custos |
| `docs/INGESTAO_DADOS.md` | Detalhes do pipeline de ingestão Meta + Google + Shopify |
| `docs/HISTORICO_EVOLUCAO.md` | Timeline completa de todas as melhorias desde jun/2026 |
| `docs/arquitetura-decisoes-v2.md` | Decisões arquiteturais abertas (para revisão com Vitor) |
| `alteracoes/` | Log de cada deploy com antes/depois e impacto |

---

## 📜 Licença

Projeto proprietário — Guará Marketing. Todos os direitos reservados.

---

*README gerado em 13/10/2026 — Cesar (Agente de Performance Guará)*
