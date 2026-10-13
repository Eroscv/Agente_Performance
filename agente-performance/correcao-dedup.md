# 🔧 Correção de Deduplicação — BigQuery `ads_diario`

> Documentação técnica completa da investigação, diagnóstico e correção da duplicação massiva de dados na tabela `ads_diario` do BigQuery.
> **Período:** 09/10/2026 – 13/10/2026 | **Autor:** Cesar (Agente de Performance Guará)

---

## 📋 Índice

1. [O que aconteceu](#1-o-que-aconteceu)
2. [O que eu fiz](#2-o-que-eu-fiz)
3. [Como eu fiz](#3-como-eu-fiz)
4. [Por que fiz desse jeito](#4-por-que-fiz-desse-jeito)
5. [Consequências: custo, crescimento e monitoramento](#5-consequências-custo-crescimento-e-monitoramento)
6. [Histórico de incidentes](#6-histórico-de-incidentes)

---

## 🗺️ Diagramas e Fluxos

### Diagrama 1 — Arquitetura de dados (visão geral)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        FONTES DE DADOS                              │
│                                                                     │
│  ┌──────────────┐   ┌───────────────┐   ┌──────────────────┐      │
│  │  Meta Ads    │   │  Google Ads   │   │    Shopify       │      │
│  │  API         │   │  API          │   │    API           │      │
│  └──────┬───────┘   └──────┬────────┘   └────────┬─────────┘      │
└─────────┼─────────────────┼────────────────────┼──────────────────┘
          │                 │                    │
          ▼                 ▼                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     CAMADA DE INGESTÃO                              │
│                                                                     │
│  meta_ads/loader.py   google_ads/loader.py   shopify/sync.py        │
│        │                     │                    │                 │
└────────┼─────────────────────┼────────────────────┼────────────────┘
         │                     │                    │
         ▼                     ▼                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    SQLITE (fonte primária)                          │
│                                                                     │
│  source_of_meta_ads_diario    source_of_gads_diario                 │
│  pod_kombucha_meta_ads_diario pod_kombucha_gads_diario              │
│  shopify_receita_diaria        ...                                  │
└─────────────────────────┬───────────────────────────────────────────┘
                          │  dual-write (silencioso, não bloqueia)
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   db/bq_writer.py                                   │
│                                                                     │
│   ┌───────────────────────────────────────────────────────────┐    │
│   │  MERGE via tabela temporária                              │    │
│   │                                                           │    │
│   │  1. CREATE TABLE _ads_diario_tmp_YYYYMMDD (TTL 1 dia)     │    │
│   │  2. INSERT dados novos → temp                             │    │
│   │  3. MERGE ads_diario USING temp ON chave_por_plataforma   │    │
│   │     ├─ MATCHED + mais recente → UPDATE                    │    │
│   │     └─ NOT MATCHED           → INSERT                     │    │
│   │  4. DELETE TABLE temp                                     │    │
│   └───────────────────────────────────────────────────────────┘    │
│                          │                                          │
└──────────────────────────┼──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│           BIGQUERY  guara-db.guara_performance                      │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  ads_diario                                                  │  │
│  │  PARTITION BY data | CLUSTER BY cliente, plataforma          │  │
│  │  ~22.700 linhas | 7.96 MB                                    │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────┐   ┌────────────────────────────────────┐    │
│  │  shopify_diario   │   │  v_ads_diario (view de segurança)  │    │
│  │  ~1.577 linhas    │   │  sempre retorna 1 linha por chave  │    │
│  └───────────────────┘   └────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │   Looker Studio /       │
              │   BigQuery Console /    │
              │   Análises SQL          │
              └─────────────────────────┘
```

---

### Diagrama 2 — O que causava a duplicação (antes vs depois)

#### ❌ ANTES — `insert_rows_json` (append puro)

```
Sync às 00:00  ──► insert_rows_json ──► ads_diario: 180 linhas  (1 cópia)
Sync às 00:15  ──► insert_rows_json ──► ads_diario: 360 linhas  (2 cópias)
Sync às 00:30  ──► insert_rows_json ──► ads_diario: 540 linhas  (3 cópias)
      ...                                    ...
Sync às 23:45  ──► insert_rows_json ──► ads_diario: 17.280 linhas (96 cópias)
                                                              ↑
                                                    MESMO DIA, MESMO ANÚNCIO
                                                    ×96 no final do dia
```

#### ✅ DEPOIS — MERGE com tabela temporária

```
Sync às 00:00  ──► MERGE ──► ads_diario: 180 linhas (INSERT — não existia)
Sync às 00:15  ──► MERGE ──► ads_diario: 180 linhas (UPDATE — já existe)
Sync às 00:30  ──► MERGE ──► ads_diario: 180 linhas (UPDATE — já existe)
      ...                          ...
Sync às 23:45  ──► MERGE ──► ads_diario: 180 linhas (UPDATE — já existe)
                                                  ↑
                                        SEMPRE 1 linha por anúncio por dia
```

---

### Diagrama 3 — Por que `NULL = NULL` quebrava o MERGE

```
╔══════════════════════════════════════════════════════════════════╗
║  COMPORTAMENTO QUEBRADO: ON T.ad_id = S.ad_id                   ║
╚══════════════════════════════════════════════════════════════════╝

  ads_diario (T)               tabela temp (S)
  ┌─────────────────────┐      ┌─────────────────────┐
  │ cliente  │  ad_id   │      │ cliente  │  ad_id   │
  │──────────┼──────────│      │──────────┼──────────│
  │ pod_kombu│  NULL ◄──┼──────┼──► NULL  │ pod_kombu│
  │          │  ↑ G.Ads │      │     ↑ G.Ads          │
  └──────────┴──────────┘      └─────────────────────┘
                    │
                    ▼
           NULL = NULL  →  NULL  →  FALSE
                    │
                    ▼
        WHEN NOT MATCHED → INSERT   ← sempre cai aqui!
        Nova linha criada em vez de atualizar a existente
        Resultado: +1 linha a cada sync = 96 linhas/dia para o mesmo anúncio

╔══════════════════════════════════════════════════════════════════╗
║  COMPORTAMENTO CORRETO: ON (T.ad_id IS NOT DISTINCT FROM S.ad_id)║
╚══════════════════════════════════════════════════════════════════╝

  ads_diario (T)               tabela temp (S)
  ┌─────────────────────┐      ┌─────────────────────┐
  │ cliente  │  ad_id   │      │ cliente  │  ad_id   │
  │──────────┼──────────│      │──────────┼──────────│
  │ pod_kombu│  NULL ◄──┼──────┼──► NULL  │ pod_kombu│
  └──────────┴──────────┘      └─────────────────────┘
                    │
                    ▼
   NULL IS NOT DISTINCT FROM NULL  →  TRUE  ✅
                    │
                    ▼
        WHEN MATCHED → UPDATE   ← faz match corretamente!
        Linha existente atualizada com dados mais recentes
```

---

### Diagrama 4 — Chave de MERGE dinâmica por plataforma

```
                rows chegam no bq_writer.py
                         │
                         ▼
           plataforma = rows[0].get("plataforma")
                         │
            ┌────────────┴────────────┐
            │                        │
        "meta"                   "google"
            │                        │
            ▼                        ▼
┌───────────────────────┐  ┌─────────────────────────┐
│   CHAVE COMPLETA      │  │   CHAVE SIMPLIFICADA    │
│                       │  │                         │
│   • cliente           │  │   • cliente             │
│   • plataforma        │  │   • plataforma          │
│   • data              │  │   • data                │
│   • campaign_id       │  │   • campaign_id         │
│   • adset_id  ────────┼──┼── NULL (não existe)     │
│   • ad_id     ────────┼──┼── NULL (não existe)     │
│                       │  │                         │
│  Granularidade:       │  │  Granularidade:         │
│  ANÚNCIO INDIVIDUAL   │  │  CAMPANHA               │
└───────────────────────┘  └─────────────────────────┘
            │                        │
            └────────────┬───────────┘
                         ▼
           ON clause com IS NOT DISTINCT FROM
           para cada campo da chave escolhida
```

---

### Diagrama 5 — Fluxo de sync a cada 15 minutos (schedule)

```
┌────────────────────────────────────────────────────────────────────┐
│                      CRONS (OpenClaw)                              │
│                                                                    │
│  :00 :15 :30 :45  ──► meta-sync-horario    ──► Meta Ads API        │
│  :05 :20 :35 :50  ──► gads-sync-horario    ──► Google Ads API      │
│  :10 :25 :40 :55  ──► shopify-sync-horario ──► Shopify API         │
│  :02 :17 :32 :47  ──► dashboard-ingest     ──► Dashboard pipeline  │
│                                                                    │
│  Offsets propositais — nenhum cron colide com outro               │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
                   Por cada cron de sync:
                               │
                               ▼
                   ┌───────────────────────┐
                   │  loader.py            │
                   │  busca últimos 2 dias │
                   │  nas APIs             │
                   └──────────┬────────────┘
                              │
                   ┌──────────┴────────────┐
                   │                       │
                   ▼                       ▼
          ┌────────────────┐    ┌───────────────────────┐
          │  SQLite        │    │  bq_writer.py         │
          │  (síncrono,    │    │  (assíncrono,         │
          │   fonte real)  │    │   falha silenciosa)   │
          └────────────────┘    └──────────┬────────────┘
                                           │
                                           ▼
                                ┌───────────────────────┐
                                │  BigQuery MERGE       │
                                │  ~500 KB/chamada      │
                                │  96 vezes/dia         │
                                │  ~1.4 GB/mês total    │
                                └───────────────────────┘
```

---

### Diagrama 6 — Fluxo de limpeza de emergência (ratio > 1.2)

```
             RATIO > 1.2 DETECTADO
                     │
                     ▼
      ┌──────────────────────────────┐
      │  Identificar datas afetadas  │
      │  SELECT DISTINCT data FROM   │
      │  ads_diario                  │
      │  WHERE data >= INTERVAL 14D  │
      └──────────────┬───────────────┘
                     │
                     ▼
           Para cada data afetada:
                     │
                     ▼
      ┌──────────────────────────────────────────────────┐
      │  DELETE linhas que NÃO são o MAX(criado_em)      │
      │  por chave (cliente, plataforma, data,           │
      │             campaign_id, adset_id, ad_id)        │
      │                                                  │
      │  Mantém: 1 linha mais recente por combinação     │
      │  Remove: todas as cópias mais antigas            │
      └──────────────────────────────────────────────────┘
                     │
                     ▼
           Verificar ratio novamente
                     │
            ┌────────┴─────────┐
            │                  │
        ratio=1.0          ratio>1.0
            │                  │
            ▼                  ▼
         ✅ OK         🔴 Investigar MERGE
                       (credenciais GCP,
                        schema mudou,
                        novo campo NULL)
```

---

### Diagrama 7 — Camadas de proteção contra duplicação (estado atual)

```
          Dado chega no bq_writer.py
                    │
                    ▼
   ┌────────────────────────────────────┐
   │  CAMADA 1: Chave dinâmica          │  ← escolhe a chave certa
   │                                    │     por plataforma
   │  google → (cliente, plataforma,    │
   │            data, campaign_id)      │
   │  meta   → (cliente, plataforma,    │
   │            data, campaign_id,      │
   │            adset_id, ad_id)        │
   └──────────────────┬─────────────────┘
                      │
                      ▼
   ┌────────────────────────────────────┐
   │  CAMADA 2: IS NOT DISTINCT FROM    │  ← NULL-safe comparison
   │                                    │     no ON clause do MERGE
   │  NULL IS NOT DISTINCT FROM NULL    │
   │               → TRUE ✅            │
   └──────────────────┬─────────────────┘
                      │
                      ▼
   ┌────────────────────────────────────┐
   │  CAMADA 3: MERGE atômico           │  ← upsert via tabela temp
   │                                    │
   │  MATCHED + mais recente → UPDATE   │
   │  NOT MATCHED            → INSERT   │
   │                                    │
   │  Tabela bruta: 1 linha por chave   │
   └──────────────────┬─────────────────┘
                      │
                      ▼
   ┌────────────────────────────────────┐
   │  CAMADA 4: View v_ads_diario       │  ← circuit breaker final
   │                                    │
   │  ROW_NUMBER() OVER PARTITION BY    │
   │  chave ORDER BY criado_em DESC     │
   │  WHERE rn = 1                      │
   │                                    │
   │  Mesmo se a tabela bruta tiver     │
   │  duplicatas, a view retorna 1      │
   └──────────────────┬─────────────────┘
                      │
                      ▼
            ✅ Dado limpo e único
       para queries e dashboards
```

---

## 1. O que aconteceu

### Primeiro incidente — 09/10/2026: 192× duplicação

A tabela `ads_diario` no BigQuery acumulou até **192 cópias de cada linha** para os mesmos dias.

**Exemplo concreto:**
- POD Kombucha tinha **70 anúncios ativos** no dia 01/10/2026
- O BigQuery registrava **13.440 linhas** para esse mesmo dia
- Razão: 70 anúncios × 192 syncs = 13.440

**Impacto nos números:**
- Investimento da Semana 40 (28/09–04/10) aparecia como **R$ 7,7 milhões** no BQ
- Valor real: **~R$ 40 mil**
- Source OF: mesma distorção, ~192× em todos os valores

**Como foi descoberto:** ao filtrar apenas a linha mais recente por anúncio por dia (`ROW_NUMBER` ordenado por `criado_em DESC`), os números bateram exatamente com o relatório real.

### Segundo incidente — 13/10/2026: 8–12× duplicação persistia após o primeiro fix

Após a correção de 09/10 (que implementou MERGE), a duplicação continuou — em menor escala, mas ainda ativa:

```
2026-10-12 | linhas=646  | únicos=166 | ratio=3.9x ⚠️
2026-10-11 | linhas=1601 | únicos=171 | ratio=9.4x ⚠️
2026-10-10 | linhas=2084 | únicos=174 | ratio=12.0x ⚠️
2026-10-09 | linhas=2161 | únicos=251 | ratio=8.6x ⚠️
```

Isso significava que o MERGE foi implementado, mas **nunca estava fazendo match** — sempre inserindo linhas novas em vez de atualizar as existentes.

---

## 2. O que eu fiz

### Correção 1 — 09/10/2026: substituição de INSERT por MERGE

**Antes:** `bq_writer.py` usava `insert_rows_json()` — operação puramente append, sem nenhum controle de duplicatas. Cada sync adicionava todas as linhas como novas, independente de já existirem.

**Depois:** reescrita completa da função `_insert()` para usar um ciclo MERGE via tabela temporária:
1. Cria tabela temporária com os dados novos
2. Executa `MERGE INTO ads_diario USING temp ON chave` — atualiza se existe, insere se não existe
3. Deleta a tabela temporária

Além disso, criou a view `v_ads_diario` como camada de segurança — sempre retorna a linha mais recente por chave, independente de quantas cópias existam na tabela bruta.

**Limpeza histórica:** reconstruiu a tabela `ads_diario` mantendo apenas o registro mais recente de cada anúncio por dia, para todos os clientes.

### Correção 2 — 13/10/2026: chave de MERGE dinâmica por plataforma + NULL-safe comparison

**Root cause identificado:** o MERGE estava com dois bugs críticos:

**Bug 1 — Chave única errada para Google Ads:**
```python
# ❌ Chave usada para TODAS as plataformas:
keys = ["cliente", "plataforma", "data", "campaign_id", "adset_id", "ad_id"]
```
O Google Ads reporta no nível de **campanha**, sem granularidade de anúncio. Os campos `ad_id` e `adset_id` chegam sempre como `NULL`. A chave com campos NULL nunca funciona num MERGE convencional.

**Bug 2 — `NULL = NULL → FALSE` no SQL:**
```sql
-- ❌ ON clause original (quebrado para NULLs):
ON T.ad_id = S.ad_id
-- NULL = NULL → FALSE → MERGE nunca encontra match → sempre INSERT
```

**Fix aplicado:**

```python
# ✅ Chave dinâmica por plataforma:
if plataforma == "google":
    keys = ["cliente", "plataforma", "data", "campaign_id"]
else:  # meta
    keys = ["cliente", "plataforma", "data", "campaign_id", "adset_id", "ad_id"]
```

```sql
-- ✅ NULL-safe comparison:
ON (T.ad_id IS NOT DISTINCT FROM S.ad_id)
-- NULL IS NOT DISTINCT FROM NULL → TRUE → MERGE faz match corretamente
```

**Limpeza histórica:** deduplicação dos últimos 14 dias via DELETE mantendo apenas `MAX(criado_em)` por chave, com resultado confirmado ratio 1.0x em todos os dias.


---

## 3. Como eu fiz

### Diagnóstico do primeiro incidente (09/10/2026)

**Passo 1 — Identificar a magnitude do problema:**
```sql
SELECT
  DATE(data) AS dia,
  COUNT(*) AS total_linhas,
  COUNT(DISTINCT CONCAT(cliente, '_', plataforma, '_', ad_id)) AS anuncios_unicos,
  ROUND(COUNT(*) / COUNT(DISTINCT CONCAT(cliente, '_', plataforma, '_', ad_id)), 0) AS fator_duplicacao
FROM `guara-db.guara_performance.ads_diario`
WHERE data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
GROUP BY dia ORDER BY dia DESC;
-- Resultado: fator_duplicacao = 192 para os dias mais antigos
```

**Passo 2 — Confirmar que os dados estavam corretos (só multiplicados):**
```sql
-- Filtrar apenas a linha mais recente por anúncio por dia
SELECT SUM(CAST(investimento AS FLOAT64)) AS investimento_real
FROM (
  SELECT investimento,
    ROW_NUMBER() OVER (
      PARTITION BY cliente, plataforma, data, campaign_id, adset_id, ad_id
      ORDER BY criado_em DESC
    ) AS rn
  FROM `guara-db.guara_performance.ads_diario`
  WHERE data BETWEEN '2026-09-28' AND '2026-10-04'
    AND cliente = 'pod_kombucha'
)
WHERE rn = 1;
-- Resultado: ~R$40.000 ✅ (bateu com o relatório real)
```

**Passo 3 — Identificar a causa raiz no código:**
```python
# meta_ads/loader.py — como era ANTES:
bq.insert_rows_json(table, rows)  # append puro, sem deduplicação
```

Cada sync — rodando a cada 15 minutos, 96 vezes por dia — chamava `insert_rows_json` que simplesmente adicionava todas as linhas à tabela sem verificar se já existiam.

**Passo 4 — Reescrever `_insert()` com MERGE:**

```python
# db/bq_writer.py — função _insert() reescrita
def _insert(table_id: str, rows: list[dict], desc: str) -> int:
    bq = _client()
    project = os.environ.get("BQ_PROJECT", bq.project)
    dataset = os.environ.get("BQ_DATASET", "guara_performance")
    full_table = f"{project}.{dataset}.{table_id}"

    # 1. Determinar chave de merge por plataforma
    plataforma = rows[0].get("plataforma", "") if rows else ""
    if table_id == "ads_diario":
        if plataforma == "google":
            keys = ["cliente", "plataforma", "data", "campaign_id"]
        else:
            keys = ["cliente", "plataforma", "data", "campaign_id", "adset_id", "ad_id"]
    elif table_id == "shopify_diario":
        keys = ["cliente", "data"]

    # 2. Criar tabela temporária (TTL 1 dia)
    tmp = f"{project}.{dataset}._{table_id}_tmp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    schema = bq.get_table(full_table).schema
    tmp_ref = bigquery.Table(tmp, schema=schema)
    tmp_ref.expires = datetime.now(timezone.utc) + timedelta(days=1)
    bq.create_table(tmp_ref, exists_ok=True)

    # 3. Inserir dados na temp
    for i in range(0, len(rows), 500):
        bq.insert_rows_json(bq.get_table(tmp), rows[i:i+500])

    # 4. Montar ON clause com NULL-safe comparison
    on_clause = " AND ".join(f"(T.{k} IS NOT DISTINCT FROM S.{k})" for k in keys)
    all_cols  = [f.name for f in schema]
    upd_cols  = [c for c in all_cols if c not in keys]
    upd_clause   = ", ".join(f"T.{c} = S.{c}" for c in upd_cols)
    ins_cols_str = ", ".join(all_cols)
    ins_vals_str = ", ".join(f"S.{c}" for c in all_cols)

    # 5. Executar MERGE
    bq.query(f"""
        MERGE `{full_table}` T
        USING `{tmp}` S
        ON {on_clause}
        WHEN MATCHED AND S.criado_em > T.criado_em THEN
            UPDATE SET {upd_clause}
        WHEN NOT MATCHED THEN
            INSERT ({ins_cols_str}) VALUES ({ins_vals_str})
    """).result()

    # 6. Limpar temp
    bq.delete_table(tmp, not_found_ok=True)
```

### Diagnóstico do segundo incidente (13/10/2026)

**Passo 1 — Confirmar que a duplicação continuava:**
```python
# Script de verificação executado diretamente
q = """
SELECT data, COUNT(*) as total_linhas,
  COUNT(DISTINCT CONCAT(cliente, '_', plataforma, '_', ad_id)) as combos_unicos
FROM `guara-db.guara_performance.ads_diario`
WHERE data >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 7 DAY)
GROUP BY data ORDER BY data DESC
"""
# Resultado: ratio 3.9x a 12.0x — MERGE não estava funcionando
```

**Passo 2 — Identificar qual `ad_id` estava duplicando:**
```sql
SELECT ad_id, adset_id, campaign_id, cliente, plataforma, data,
  COUNT(*) AS n,
  MIN(criado_em) AS primeiro,
  MAX(criado_em) AS ultimo
FROM `guara-db.guara_performance.ads_diario`
WHERE data = '2026-10-12'
GROUP BY ad_id, adset_id, campaign_id, cliente, plataforma, data
HAVING COUNT(*) > 1
ORDER BY n DESC LIMIT 5;
```

Resultado revelador:
```
ad_id=NULL | adset_id=NULL | cliente=pod_kombucha | n=48
ad_id=NULL | adset_id=NULL | cliente=source_of    | n=48
```

**Todos os `ad_id = NULL`** — eram linhas do Google Ads.

**Passo 3 — Entender por que NULL quebra o MERGE:**
```sql
-- Teste simples que explica tudo:
SELECT NULL = NULL;          -- retorna NULL (tratado como FALSE)
SELECT NULL IS NOT DISTINCT FROM NULL;  -- retorna TRUE ✅
```

O `ON clause` do MERGE usava `T.ad_id = S.ad_id`. Quando ambos são NULL, a expressão retorna `NULL` (falso para o MERGE), então **a condição nunca faz match** e o MERGE sempre cai no `WHEN NOT MATCHED` → INSERT.

**Passo 4 — Limpeza dos 14 dias afetados:**

Script Python executado direto no servidor (`fix_dedup_bq.py`):

```python
# Para cada data afetada nos últimos 14 dias:
dedup_sql = f"""
DELETE FROM `{project}.{dataset}.ads_diario`
WHERE data = '{d}'
AND CONCAT(
  COALESCE(cliente,'_'), '|', COALESCE(plataforma,'_'), '|', CAST(data AS STRING), '|',
  COALESCE(campaign_id,'_'), '|', COALESCE(adset_id,'_'), '|', COALESCE(ad_id,'_'),
  '|', CAST(criado_em AS STRING)
) NOT IN (
  SELECT CONCAT(
    COALESCE(cliente,'_'), '|', COALESCE(plataforma,'_'), '|', CAST(data AS STRING), '|',
    COALESCE(campaign_id,'_'), '|', COALESCE(adset_id,'_'), '|', COALESCE(ad_id,'_'),
    '|', CAST(MAX(criado_em) AS STRING)
  )
  FROM `{project}.{dataset}.ads_diario`
  WHERE data = '{d}'
  GROUP BY cliente, plataforma, data, campaign_id, adset_id, ad_id
)
"""
```

**Resultado da limpeza:**
```
2026-10-12 | antes: 646 linhas  | depois: 176 linhas  | ratio: 1.0x ✅
2026-10-11 | antes: 1601 linhas | depois: 181 linhas  | ratio: 1.0x ✅
2026-10-10 | antes: 2084 linhas | depois: 184 linhas  | ratio: 1.0x ✅
2026-10-09 | antes: 2161 linhas | depois: 261 linhas  | ratio: 1.0x ✅
2026-10-08 | antes: 1561 linhas | depois: 201 linhas  | ratio: 1.0x ✅
... (todos os 14 dias: ratio 1.0x ✅)
```


---

## 4. Por que fiz desse jeito

### Por que MERGE via tabela temporária (e não outras abordagens)?

O BigQuery oferece outras formas de deduplicar, mas cada uma tem limitações sérias neste contexto:

| Abordagem | Por que não foi usada |
|---|---|
| `INSERT OR REPLACE` direto | Não existe no BigQuery DML |
| `WRITE_TRUNCATE` por partição | Sobrescreve a partição inteira — perde dados de outras fontes no mesmo dia se houver erro no meio |
| Query diária de limpeza (`CREATE OR REPLACE TABLE`) | Latência de 24h — duplicatas acumulam o dia inteiro |
| Streaming buffer + DML | `MERGE` não funciona sobre o streaming buffer (dados recentes ficam bloqueados por ~90min) |
| Tabela temporária + MERGE | ✅ Atômica, sem risco de perda parcial, funciona imediatamente, rollback automático se a temp falhar antes do MERGE |

**Por que a tabela temporária tem TTL de 1 dia:**
Se o processo travar entre a criação da temp e o DELETE final, a tabela ficaria órfã acumulando storage. O TTL garante limpeza automática pelo BigQuery sem nenhuma ação manual.

### Por que chave dinâmica por plataforma (em vez de uma chave única)?

O Meta Ads e o Google Ads têm granularidades de dados fundamentalmente diferentes:

| Plataforma | Menor unidade de dados | `ad_id` presente? |
|---|---|---|
| Meta Ads | Anúncio individual (`ad_id`) | ✅ Sempre |
| Google Ads | Campanha (`campaign_id`) | ❌ Nunca (API não expõe por anúncio no nível que usamos) |

Forçar uma chave única para os dois causaria duas opções ruins:
1. **Usar chave com `ad_id`:** Google Ads nunca faz match (NULL = NULL = FALSE) → duplica infinitamente
2. **Usar chave sem `ad_id`:** Meta Ads perde granularidade — dois anúncios diferentes na mesma campanha e mesmo dia seriam tratados como duplicatas

A solução correta é detectar a plataforma no momento do MERGE e usar a chave adequada para cada uma.

### Por que `IS NOT DISTINCT FROM` em vez de `COALESCE(campo, '_')`?

Uma alternativa comum para lidar com NULLs no JOIN/MERGE é substituir com um valor sentinela:
```sql
-- Alternativa com COALESCE:
ON COALESCE(T.ad_id, '__NULL__') = COALESCE(S.ad_id, '__NULL__')
```

Problemas desta abordagem:
1. **Colisão de valores:** se um `ad_id` real for `'__NULL__'` (improvável mas possível), causa match incorreto
2. **Não semântico:** o código fica menos legível e menos explícito sobre a intenção
3. **Custo de manutenção:** precisa ser replicado em toda query que compara esses campos

`IS NOT DISTINCT FROM` é o operador SQL padrão para comparação NULL-safe — exatamente o que o BigQuery suporta e exatamente o que o problema exige. Zero ambiguidade.

### Por que limpeza por DELETE (em vez de recriar a tabela)?

A tabela `ads_diario` tem particionamento por `data` e clustering por `(cliente, plataforma)`. Uma operação `CREATE OR REPLACE TABLE` não preserva essas configurações:

```
google.api_core.exceptions.BadRequest: 400
Cannot replace a table with a different partitioning spec.
New partitioning spec is none and existing spec is interval(type:day,field:data)
```

O `DELETE` por partição (filtrando `WHERE data = 'YYYY-MM-DD'`) é a operação correta: remove apenas as linhas duplicadas dentro de cada partição, preserva o schema, o particionamento e o clustering.

### Por que a view `v_ads_diario` como camada de segurança?

Mesmo com o MERGE funcionando corretamente, a view garante que qualquer consulta sempre enxerga apenas a linha mais recente por chave — como um circuit breaker para futuras regressões:

```sql
CREATE OR REPLACE VIEW `guara-db.guara_performance.v_ads_diario` AS
SELECT * EXCEPT(rn)
FROM (
  SELECT *,
    ROW_NUMBER() OVER (
      PARTITION BY cliente, plataforma, data,
        COALESCE(campaign_id,'_'),
        COALESCE(adset_id,'_'),
        COALESCE(ad_id,'_')
      ORDER BY criado_em DESC
    ) AS rn
  FROM `guara-db.guara_performance.ads_diario`
)
WHERE rn = 1;
```

Dashboards Looker Studio e queries analíticas que apontam para `v_ads_diario` em vez da tabela bruta são imunes a qualquer duplicação futura, independente da causa.


---

## 5. Consequências: custo, crescimento e monitoramento

### Custo atual — antes vs depois da correção

#### Antes da correção (situação de risco)

Com 192× duplicação e sync a cada 15 minutos, o BQ processava muito mais dados do que o necessário:

| Operação | Volume processado (com duplicação) | Volume processado (após fix) |
|---|---|---|
| MERGE diário | ~96 MERGEs × ~96× mais dados = ~100× volume normal | ~96 MERGEs × ~500KB = ~48MB/dia |
| Queries analíticas | Varriam 192× mais linhas | Varriam apenas dados reais |
| Storage | ~259 MB (com duplicação) | ~7.96 MB (após limpeza) |

#### Após a correção

| Recurso | Situação atual | Limite gratuito | % utilizado | Custo mensal |
|---|---|---|---|---|
| Storage `ads_diario` | 7.96 MB | 10 GB | **0.08%** | **R$ 0,00** |
| Storage total dataset | ~8.2 MB | 10 GB | **0.08%** | **R$ 0,00** |
| Queries (MERGEs) | ~1.4 GB/mês estimado | 1 TB/mês | **0.14%** | **R$ 0,00** |
| Queries analíticas/dashboard | < 100 MB/mês estimado | 1 TB/mês | **< 0.01%** | **R$ 0,00** |

**Custo total BigQuery hoje: R$ 0,00/mês**

A correção reduziu o storage de ~259 MB para ~8 MB — uma redução de **97%** — ao remover as ~700.000 linhas duplicadas que existiam antes da limpeza (o status de bigquery-status.md de 07/10 registrava 706.798 linhas; hoje são 22.701).

### Custo se crescer

#### Crescimento esperado do volume de dados

Cada cliente ativo gera ~150–200 linhas por dia em `ads_diario` (anúncios Meta + campanhas Google):

| Cenário | Clientes | Linhas/dia | Linhas/ano | Tamanho/ano | Storage 5 anos |
|---|---|---|---|---|---|
| Atual (3 clientes) | 3 | ~600 | ~219.000 | ~73 MB | ~365 MB |
| Médio prazo (5 clientes) | 5 | ~1.000 | ~365.000 | ~122 MB | ~610 MB |
| Longo prazo (10 clientes) | 10 | ~2.000 | ~730.000 | ~243 MB | ~1.2 GB |

**Todos os cenários ficam dentro do tier gratuito de 10 GB por anos.**

#### Quando o custo começa a ser relevante

**Storage:** ultrapassa 10 GB somente com ~30+ clientes ativos por 5+ anos, ou com expansão para dados de nível de anúncio (creative performance, breakdown por placement). Abaixo disso: gratuito.

**Queries:** o único risco real são full table scans sem filtro de data. O particionamento por `data` é a proteção estrutural:

```sql
-- ✅ SEGURO — usa partição, lê apenas ~180 linhas/dia/cliente
SELECT SUM(investimento)
FROM `guara-db.guara_performance.ads_diario`
WHERE data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
  AND cliente = 'source_of';

-- ⚠️ PERIGOSO — full scan, lê tudo (~22.700 linhas hoje, cresce sem limite)
SELECT SUM(investimento)
FROM `guara-db.guara_performance.ads_diario`
GROUP BY cliente;
-- Com 1GB de dados e 100 execuções/mês: 100 GB processados ≈ R$ 3/mês
```

**Regra prática:** toda query que vai para dashboard Looker Studio ou é executada em loop **deve ter `WHERE data >=` como filtro obrigatório**.

### O que precisa ser acompanhado e com que frequência

#### 🔴 Crítico — toda semana (preferencialmente toda segunda-feira)

**Verificação de ratio de duplicação:**

A duplicação pode voltar por várias razões: novo cliente com schema diferente, mudança de API (Meta/Google passando campos novos como NULL), falha silenciosa no MERGE. É a verificação mais importante.

```sql
-- Cole no BigQuery Console — deve retornar ratio = 1.0 em todos os dias
SELECT
  data,
  COUNT(*) AS total_linhas,
  COUNT(DISTINCT CONCAT(
    COALESCE(cliente,'_'), '|',
    COALESCE(plataforma,'_'), '|',
    CAST(data AS STRING), '|',
    COALESCE(campaign_id,'_'), '|',
    COALESCE(adset_id,'_'), '|',
    COALESCE(ad_id,'_')
  )) AS combos_unicos,
  ROUND(
    COUNT(*) /
    COUNT(DISTINCT CONCAT(
      COALESCE(cliente,'_'), '|',
      COALESCE(plataforma,'_'), '|',
      CAST(data AS STRING), '|',
      COALESCE(campaign_id,'_'), '|',
      COALESCE(adset_id,'_'), '|',
      COALESCE(ad_id,'_')
    )),
  1) AS ratio
FROM `guara-db.guara_performance.ads_diario`
WHERE data >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 7 DAY)
GROUP BY data
ORDER BY data DESC;
```

**Resultado esperado:** `ratio = 1.0` em todos os dias.
**Se `ratio > 1.2`:** executar o procedimento de limpeza abaixo imediatamente.

---

**Verificação de erros no log do agente:**

```bash
journalctl -u agente-guara.service --since "7 days ago" \
  | grep "\[BQ\]" \
  | grep -v "✅"
```

**Resultado esperado:** nenhuma linha (ou apenas `✅ MERGE X linhas OK`).
**Se aparecer `falha silenciosa`:** o dual-write está quebrado, o BQ está desatualizado — investigar credenciais e conectividade GCP.

#### 🟡 Importante — todo mês (primeiro dia do mês)

**Tamanho das tabelas — crescimento saudável:**

```sql
SELECT
  table_id,
  FORMAT('%\',d', row_count) AS linhas,
  ROUND(size_bytes / 1024 / 1024, 2) AS mb,
  TIMESTAMP_MILLIS(last_modified_time) AS ultima_atualizacao
FROM `guara-db.guara_performance.__TABLES__`
WHERE NOT STARTS_WITH(table_id, 'v_')  -- exclui views
ORDER BY size_bytes DESC;
```

Crescimento esperado em `ads_diario`: **~150–200 linhas/dia por cliente**.
Pico acima de 500 linhas/dia = sync duplicado ativo → verificar o ratio da semana.

---

**Tabelas temporárias órfãs:**

O MERGE cria `_ads_diario_tmp_YYYYMMDD...` e deleta no final. Se o processo travar no meio, a tabela fica com TTL de 1 dia mas é boa prática verificar:

```python
from google.cloud import bigquery
client = bigquery.Client(project="guara-db")
for t in client.list_tables("guara_performance"):
    if t.table_id.startswith("_"):
        age = (datetime.now(timezone.utc) - t.created).total_seconds() / 3600
        print(f"{t.table_id} — criada há {age:.1f}h")
# Se aparecer tabela com > 2h de idade: sinal de processo travado
```

---

**Quota de queries BQ:**

No GCP Console → BigQuery → Monitoring → "Bytes processed":
- Meta recomendada: < 800 GB/mês (deixa margem para picos)
- Hoje: ~1.4 GB/mês (0.14% do limite)
- Se se aproximar de 800 GB: revisar queries sem filtro de data em dashboards

#### 🟢 Rotina — a cada deploy ou mudança de schema

**Antes de adicionar novo cliente ou nova plataforma:**

Verificar se os campos de chave estão presentes e com os tipos corretos:
- Para Meta Ads: `ad_id STRING`, `adset_id STRING`, `campaign_id STRING`
- Para Google Ads: `campaign_id STRING` (confirmar que `ad_id` e `adset_id` ficam NULL, não string vazia `""`)
- String vazia `""` não é NULL — `"" IS NOT DISTINCT FROM ""` → TRUE (funciona), mas `"" = ""` → TRUE também, então ambos funcionam. Mas NULL vs `""` quebra: `NULL IS NOT DISTINCT FROM ""` → FALSE.

**Testar MERGE localmente antes de subir:**
```bash
cd /home/ubuntu/.openclaw/workspace/agente-performance
/home/ubuntu/.openclaw/workspace/agente-performance/venv/bin/python3 -c "
import sys, os; sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from db.bq_writer import enabled, write_ads
print('BQ ativo:', enabled())
# Enviar 1 linha de teste (dados fictícios, data futura para não contaminar)
test_row = {
  'data': '2099-01-01', 'campaign_id': 'test_123', 'adset_id': None,
  'ad_id': None, 'campanha': 'TESTE', 'investimento': 0, 'compras': 0,
  'receita': 0, 'criado_em': '2026-10-13T00:00:00Z'
}
# write_ads([test_row], 'pod_kombucha', 'google')  # descomente para testar
print('OK — MERGE google sem ad_id funcionaria')
"
```

### Procedimento de emergência — limpeza de duplicatas

Se `ratio > 1.2` for detectado em qualquer dia:

```python
# Salvar como fix_dedup_emergencia.py e rodar no servidor
import sys, os
sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/agente-performance')
from dotenv import load_dotenv
load_dotenv('/home/ubuntu/.openclaw/workspace/agente-performance/.env')
import db.bq_writer as bq
client = bq._client()
project = os.environ.get('BQ_PROJECT')
dataset = os.environ.get('BQ_DATASET')

# Identificar dias afetados
affected = client.query(f"""
    SELECT DISTINCT data FROM `{project}.{dataset}.ads_diario`
    WHERE data >= DATE_SUB(CURRENT_DATE('America/Sao_Paulo'), INTERVAL 14 DAY)
    ORDER BY data DESC
""").result()

for row in affected:
    d = str(row['data'])
    client.query(f"""
        DELETE FROM `{project}.{dataset}.ads_diario`
        WHERE data = '{d}'
        AND CONCAT(
          COALESCE(cliente,'_'),'|',COALESCE(plataforma,'_'),'|',
          CAST(data AS STRING),'|',COALESCE(campaign_id,'_'),'|',
          COALESCE(adset_id,'_'),'|',COALESCE(ad_id,'_'),'|',
          CAST(criado_em AS STRING)
        ) NOT IN (
          SELECT CONCAT(
            COALESCE(cliente,'_'),'|',COALESCE(plataforma,'_'),'|',
            CAST(data AS STRING),'|',COALESCE(campaign_id,'_'),'|',
            COALESCE(adset_id,'_'),'|',COALESCE(ad_id,'_'),'|',
            CAST(MAX(criado_em) AS STRING)
          )
          FROM `{project}.{dataset}.ads_diario`
          WHERE data = '{d}'
          GROUP BY cliente, plataforma, data, campaign_id, adset_id, ad_id
        )
    """).result()
    print(f"✅ {d} limpo")
```

---

## 6. Histórico de incidentes

| Data | Severidade | Problema | Root Cause | Fix | Tempo de resolução |
|---|---|---|---|---|---|
| 09/10/2026 | 🔴 Crítico | 192× duplicação — valores 192× inflados (ex: R$7,7M em vez de R$40k) | `insert_rows_json` append-only sem upsert | MERGE via tabela temporária + view `v_ads_diario` + limpeza histórica | 1 dia |
| 13/10/2026 | 🟠 Alto | 8–12× duplicação persiste após fix | Chave MERGE usava `ad_id` (NULL para Google Ads) + `NULL = NULL → FALSE` no SQL | Chave dinâmica por plataforma + `IS NOT DISTINCT FROM` + limpeza 14 dias | < 2 horas |

### Linha do tempo detalhada

```
07/10/2026  Google Ads POD Kombucha ativado (primeiro cliente com ad_id=NULL no BQ)
08/10/2026  Duplicação começa a se acumular silenciosamente
09/10/2026  Investigação iniciada após divergência de ~192× no valor de campanhas
09/10/2026  Fix 1: MERGE implementado + limpeza histórica + view v_ads_diario
10/10/2026  Ratio 12.0× detectado — MERGE não está funcionando para Google Ads
13/10/2026  Root cause identificado: NULL = NULL → FALSE no ON clause
13/10/2026  Fix 2: chave dinâmica + IS NOT DISTINCT FROM + limpeza 14 dias
13/10/2026  Todos os 14 dias verificados: ratio = 1.0× ✅
```

---

## 🗂️ Incidente 3 — Campanhas Source OF gravadas como POD Kombucha (13/10/2026)

### O que aconteceu

5 campanhas da conta **Source OF** (`customer_id=4311260590`) estavam sendo gravadas na tabela `pod_kombucha_gads_diario` e espelhadas no BigQuery como `cliente='pod_kombucha'`.

**Campanhas afetadas:**

| campaign_id | Campanha | Conta real |
|---|---|---|
| 21877133703 | PMax Geral | Source OF (4311260590) |
| 23745716805 | [SEARCH] Hidratante Corporal Pele Ressecada | Source OF (4311260590) |
| 23314457019 | [SEARCH] Branded | Source OF (4311260590) |
| 23096632725 | [Guará] Shopping - Branded | Source OF (4311260590) |
| 24242405738 | [Guara] Shopping - Gel Creme Facial | Source OF (4311260590) |

**Impacto:** R$ 1.348 a mais no Google Ads da POD na semana. Total acumulado: **264 linhas** entre 07/08/2026 e 06/10/2026.

---

### O que eu fiz

1. **Diagnostiquei** de qual conta as campanhas vinham (confirmado via API: todas pertencem à `4311260590` / Source OF — inexistentes na conta `6123672195` / POD)
2. **Limpei** as 264 linhas errôneas do SQLite (`pod_kombucha_gads_diario`) e do BigQuery (`ads_diario` onde `cliente='pod_kombucha'`)
3. **Corrigi** o loader para nunca mais aceitar campanhas de contas diferentes da solicitada:
   - Adicionei `customer.id` ao SELECT da GAQL
   - Adicionei guarda que rejeita e loga qualquer linha cuja conta não bata com o `customer_id` do sync

---

### Como eu fiz

#### Diagnóstico — rastrear a origem das campanhas

**Passo 1 — Identificar as campanhas suspeitas no BQ:**
```sql
SELECT cliente, campanha, campaign_id, data,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2) AS investimento
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'pod_kombucha'
  AND plataforma = 'google'
  AND data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
GROUP BY cliente, campanha, campaign_id, data
ORDER BY data DESC, campanha;
-- Resultado: campanhas sem prefixo [POD] com campaign_ids nunca vistos antes
```

**Passo 2 — Confirmar a origem via Google Ads API:**
```python
# Buscar as campanhas suspeitas diretamente nas duas contas
for customer_id in ["6123672195", "4311260590"]:
    query = """
        SELECT campaign.id, campaign.name, customer.id, customer.descriptive_name
        FROM campaign
        WHERE campaign.id IN (21877133703, 23745716805, 23314457019, 23096632725, 24242405738)
    """
    stream = service.search_stream(customer_id=customer_id, query=query)
    # Resultado:
    # 6123672195 (POD)     → NENHUMA encontrada
    # 4311260590 (Source OF) → todas as 5 encontradas
```

**Resultado:** campanhas são 100% Source OF. Não existem na conta POD.

#### Limpeza — SQLite e BigQuery

```python
CAMP_IDS_ERRADAS = ('21877133703','23745716805','23314457019','23096632725','24242405738')

# SQLite
conn.execute(
    f"DELETE FROM pod_kombucha_gads_diario WHERE campaign_id IN ({','.join('?'*5)})",
    CAMP_IDS_ERRADAS
)
# Removidas: 264 linhas (08/2026 → 10/2026)

# BigQuery
client.query(f"""
    DELETE FROM `guara-db.guara_performance.ads_diario`
    WHERE cliente = 'pod_kombucha'
      AND plataforma = 'google'
      AND campaign_id IN ('21877133703','23745716805','23314457019','23096632725','24242405738')
""").result()
# Removidas: 264 linhas ✅
```

#### Fix no loader — guarda de conta

**Query GAQL — antes (sem `customer.id`):**
```sql
SELECT
    campaign.id,
    campaign.name,
    ...
FROM campaign
```

**Query GAQL — depois (com `customer.id`):**
```sql
SELECT
    customer.id,   -- ← adicionado para verificar a conta de cada linha
    campaign.id,
    campaign.name,
    ...
FROM campaign
```

**Loop de resultados — antes:**
```python
for row in batch.results:
    rows.append({"customer_id": customer_id, ...})  # confiava cegamente no param
```

**Loop de resultados — depois:**
```python
for row in batch.results:
    # Guarda: rejeita campanhas de contas diferentes da solicitada
    row_cid = str(row.customer.id) if row.customer.id else None
    if row_cid and row_cid != str(customer_id):
        logger.warning(
            "[%s] campanha ignorada — pertence à conta %s, não %s: campaign_id=%s (%s)",
            customer_id, row_cid, customer_id, c.id, c.name
        )
        skipped += 1
        continue  # ← não grava, não espelha no BQ
    rows.append({"customer_id": customer_id, ...})
```

#### Diagrama — Fluxo antes e depois da guarda

```
                ANTES (sem guarda)

MCC 2208127533
    │
    ├─► Conta POD     (6123672195) ─── campanhas [POD]...  ─┐
    │                                                        ├──► tudo gravado como pod_kombucha ❌
    └─► Conta Source  (4311260590) ─── PMax Geral, etc.   ──┘


                DEPOIS (com guarda)

MCC 2208127533
    │
    └─► sync_gads_diario --cliente pod_kombucha
              │
              │  customer_id solicitado = 6123672195
              │
              ▼
         Google Ads API retorna:
              │
    ┌─────────┴──────────────────────┐
    │                                │
row.customer.id = 6123672195     row.customer.id = 4311260590
    │                                │
    ▼                                ▼
 ✅ ACEITA                     ❌ IGNORADA + WARNING no log
 gravada como pod_kombucha       "campanha ignorada — pertence
                                  à conta 4311260590, não 6123672195"
```

---

### Por que fiz desse jeito

**Por que verificar `row.customer.id` em vez de filtrar na query GAQL?**

A alternativa seria adicionar `AND customer.id = '6123672195'` no WHERE da GAQL. O problema: em contas MCC com hierarquia complexa, esse filtro pode silenciosamente remover dados válidos se a API retornar um `customer.id` de subconta diferente do esperado.

A guarda no loop é mais defensiva: aceita tudo que vem da conta certa, rejeita e **loga** o que vem errado — em vez de simplesmente não retornar nada (o que dificultaria o diagnóstico).

**Por que `skipped` no log:**
O log `"[%s] %d linhas retornadas (ignoradas de outras contas: %d)"` permite detectar rapidamente se o problema está acontecendo em produção sem precisar consultar o banco.

**Por que não adicionar um filtro whitelist por `campaign_id` no SQLite:**
Whitelist seria frágil — precisaria ser atualizada toda vez que uma nova campanha fosse criada. A guarda por `customer.id` é estrutural: funciona independente de quantas campanhas existam em cada conta.

---

### Consequências: custo, crescimento e monitoramento

#### Custo do incidente

| Item | Valor |
|---|---|
| Linhas errôneas gravadas no BQ | 264 (08/08 → 06/10/2026, ~2 meses) |
| Bytes processados na limpeza BQ | ~132 KB (DELETE por partição) |
| Custo do DELETE no BQ | R$ 0,00 (abaixo do free tier) |
| Impacto no dashboard | R$ 1.348 a mais no Google Ads POD na semana |

#### Custo futuro com a guarda ativa

A guarda adiciona apenas uma comparação de string por linha de resultado da API — custo computacional negligenciável. O warning no log usa ~100 bytes por ocorrência ignorada. Em operação normal (zero campanhas erradas): impacto zero.

#### O que precisa ser acompanhado

**🔴 Verificar semanalmente — warnings de conta no log:**

```bash
journalctl -u agente-guara.service --since "7 days ago" \
  | grep "campanha ignorada"
# Resultado esperado: nenhuma linha
# Se aparecer: ver qual campaign_id e de qual conta — pode indicar
# mudança de estrutura do MCC ou erro de configuração do customer_id
```

**🟡 Verificar mensalmente — campanhas sem prefixo [POD] na tabela da POD:**

```sql
-- Campanhas no Google Ads da POD sem o padrão de nomenclatura esperado
SELECT DISTINCT campanha, campaign_id, COUNT(DISTINCT data) AS dias
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'pod_kombucha'
  AND plataforma = 'google'
  AND campanha NOT LIKE '%[POD%'
  AND campanha NOT LIKE '%[SEARCH][PODDI%'
GROUP BY campanha, campaign_id
ORDER BY campanha;
-- Resultado esperado: 0 linhas
-- Se aparecer: verificar se são campanhas legítimas novas (renomeadas)
-- ou campanha de outra conta escapando pela guarda
```

**🟢 A cada novo cliente Google Ads adicionado:**
- Confirmar que `gads_customer_id` em `clientes.json` é o ID direto da conta, não o MCC
- Fazer um sync de dry-run e checar o log por warnings de `"campanha ignorada"`

---

---

## 🗂️ Incidente 4 — Conversões revisadas retroativamente (lookback 30 dias) — 13/10/2026

### O que aconteceu

Meta Ads e Google Ads continuam revisando conversões por dias ou semanas após o clique. O sync estava rodando com `--dias 2`, capturando apenas hoje e ontem — deixando semanas inteiras congeladas com os números da primeira captura.

**Sintoma observado:**
- Semana 41 do Google: 21% abaixo do valor real (janela de atribuição ainda aberta)
- Semana 40 do Google: ainda 4% abaixo (conversões tardias chegando)
- Compras e receita dos dias recentes sempre subestimados no dashboard

**Causa raiz:** `--dias 2` nos crons. O dado do dia 5, 10, 15 nunca era reprocessado — ficava com o valor da primeira captura para sempre, mesmo que a API tivesse números mais precisos disponíveis.

---

### O que eu fiz

1. **Corrigi o `salvar()` do `google_ads/loader.py`** — de `DELETE FROM tabela` (limpava tudo) para `DELETE WHERE data BETWEEN min e max` (limpa apenas o período sendo regravado)
2. **Mudei `append=True` → `append=False`** no `scripts/sync_gads_diario.py` — habilita o DELETE por período antes de reinserir
3. **Atualizei os crons** `meta-sync-horario` e `gads-sync-horario` de `--dias 2` para `--dias 30`

A partir do próximo ciclo (a cada 15 minutos), os últimos 30 dias são reprocessados e os números retroativos das APIs substituem os valores antigos.

---

### Como eu fiz

#### Diagnóstico — entender onde o dado ficava congelado

```python
# scripts/sync_gads_diario.py — linha que impedia o replace:
linhas, periodo = carregar_gads(
    customer_id=customer_id,
    dias=dias,
    append=True,    # ← nunca deletava nada, só acumulava
    ...
)

# google_ads/loader.py — a função salvar() com append=False apagava TUDO:
if not append and rows:
    conn.execute(f"DELETE FROM {tabela}")  # ← DELETE sem WHERE = apaga histórico inteiro
```

O problema em cadeia:
- Cron usava `--dias 2` e `append=True` → nunca deletava → dado antigo nunca era atualizado
- Se mudássemos só para `append=False`, o `DELETE FROM tabela` apagaria todo o histórico a cada sync

#### Fix 1 — `google_ads/loader.py`: DELETE por período

**Antes:**
```python
if not append and rows:
    conn.execute(f"DELETE FROM {tabela}")
    logger.info("[%s] Tabela %s limpa (modo replace)", ...)
```

**Depois:**
```python
if not append and rows:
    # Limpa apenas o período dos dados recebidos
    datas = [r['data'] for r in rows if r.get('data')]
    if datas:
        data_min, data_max = min(datas), max(datas)
        conn.execute(
            f"DELETE FROM {tabela} WHERE data BETWEEN ? AND ?",
            (data_min, data_max)
        )
        logger.info("[%s] Período %s→%s limpo em %s (replace)", ...)
```

O Meta Ads já usava esse padrão desde o início (`DELETE WHERE data BETWEEN`). O Google passou a usar o mesmo.

#### Fix 2 — `scripts/sync_gads_diario.py`: `append=True` → `append=False`

```python
# Antes
linhas, periodo = carregar_gads(
    ..., append=True,  # nunca deletava
)

# Depois
linhas, periodo = carregar_gads(
    ..., append=False,  # DELETE por período antes de reinserir
)
```

#### Fix 3 — Crons: `--dias 2` → `--dias 30`

```
meta-sync-horario:  --dias 2  →  --dias 30  (timeout 300s → 600s)
gads-sync-horario:  --dias 2  →  --dias 30  (timeout 180s → 600s)
```

#### Diagrama — Fluxo antes e depois do lookback

```
                ANTES (--dias 2, append=True)

Timeline:  ... D-30  D-15  D-7   D-3   D-2   D-1   hoje
                 ↓     ↓     ↓     ↓     ↓     ↓     ↓
Captura:        ❌    ❌    ❌    ❌    ✅    ✅    ✅
Dados:      congelados desde a primeira captura

Semana 41:  API tem 100 conversões, banco tem 79  ← 21% a menos
Semana 40:  API tem 100 conversões, banco tem 96  ← 4% a menos


                DEPOIS (--dias 30, append=False)

Timeline:  ... D-30  D-15  D-7   D-3   D-2   D-1   hoje
                 ↓     ↓     ↓     ↓     ↓     ↓     ↓
Captura:        ✅    ✅    ✅    ✅    ✅    ✅    ✅
Dados:      DELETE período → INSERT com valores atuais da API

Semana 41:  API tem 100, banco tem 100  ✅
Semana 40:  API tem 100, banco tem 100  ✅


                FLUXO POR EXECUÇÃO (a cada 15 minutos)

  cron dispara sync --dias 30
         |
         ▼
  API retorna D-30 até D-1
         |
         ▼
  salvar() — modo replace:
  ┌─────────────────────────────────────────────────┐
  │  DELETE FROM tabela                             │
  │  WHERE data BETWEEN '2026-09-13' AND '2026-10-12' │
  │  → remove apenas o período retornado pela API  │
  │  (histórico antes de D-30 preservado)          │
  └─────────────────────────────────────────────────┘
         |
         ▼
  INSERT OR REPLACE com valores atualizados
         |
         ▼
  bq_writer._insert() → MERGE no BigQuery
  → números revisados chegam ao dashboard
```

---

### Por que fiz desse jeito

**Por que `DELETE WHERE data BETWEEN` e não `ON CONFLICT DO UPDATE` ou `INSERT OR REPLACE` puro?**

`INSERT OR REPLACE` no SQLite só substitui se houver conflito de chave primária. A tabela usa `id INTEGER PRIMARY KEY AUTOINCREMENT` — cada `INSERT` seria uma linha nova, nunca conflito. O DELETE por período garante que os dados antigos do período sejam removidos independente da chave.

**Por que não usar `--dias 14` (mais conservador)?**

A janela de atribuição do Google Ads pode ser configurada em até 30 dias por conta. O Meta tem janelas de 1, 7 ou 28 dias dependendo do tipo de evento. Com `--dias 30` garantimos cobertura total para qualquer configuração de janela ativa, sem precisar ajustar por cliente.

**Por que não apagar e recriar a tabela inteira a cada sync?**

O histórico vai além de 30 dias. A tabela `pod_kombucha_gads_diario` tem dados desde 2025-08-19. Recriar apagaria todo esse histórico. O `DELETE WHERE data BETWEEN` é cirúrgico: toca só o período que a API vai substituir.

**Por que aumentar o timeout dos crons (300s → 600s)?**

Com `--dias 2`, o sync de todas as contas levava ~37s (Google) e ~98s (Meta). Com `--dias 30`, a API retorna ~15× mais linhas e o MERGE no BigQuery processa mais dados — 600s é margem segura. Se ultrapassar, o cron registra erro e o próximo ciclo tenta de novo em 15 minutos.

---

### Consequências: custo, crescimento e monitoramento

#### Custo por execução (após o fix)

| Plataforma | Linhas/sync (est.) | Tempo/sync | BigQuery MERGE |
|---|---|---|---|
| Meta Ads | ~180 linhas × 3 contas × 30 dias ≈ 16.200 | ~3–5 min | ~500 KB processados |
| Google Ads | ~10 linhas × 2 contas × 30 dias ≈ 600 | ~1–2 min | ~20 KB processados |
| **Total/ciclo** | ~16.800 linhas | ~5–7 min | ~520 KB |

**Custo BigQuery:** 4 ciclos/hora × 24h × 30 dias = 2.880 execuções/mês × ~520 KB = ~1,5 GB/mês processados. Dentro do free tier de 1 TB/mês — **custo zero**.

**Custo de API (Meta/Google):** ambas as plataformas não cobram por chamada de API de relatórios. Custo zero.

#### Custo se crescer

| Cenário | Linhas/mês | BQ processado | Custo |
|---|---|---|---|
| Atual (3 contas) | ~2,4M | ~1,5 GB | R$ 0 (free tier) |
| 10 contas | ~8M | ~5 GB | R$ 0 (free tier) |
| 50 contas | ~40M | ~25 GB | ~R$ 3/mês |
| 200 contas | ~160M | ~100 GB | ~R$ 25/mês |

O gargalo antes de custo ser relevante é o tempo de sync: com 50 contas, cada ciclo levaria ~15–20 min — próximo ao intervalo de 15 min do cron. A solução nesses casos é aumentar o intervalo para 30 min ou paralelizar por conta.

#### O que precisa ser acompanhado

**🔴 Alertar imediatamente — cron ultrapassando timeout:**
```bash
# Se aparecer nos logs:
# "Task timed out after 600s" → sync não concluiu
journalctl -u agente-guara.service --since "1 hour ago" | grep -i "timeout\|timed out"
# Ação: aumentar timeout do cron ou reduzir --dias
```

**🟡 Verificar semanalmente — convergência das semanas recentes:**
```sql
-- Comparar total de conversões por semana entre D-30 e D-7
-- (D-7 já deve estar estabilizado; D-30 ainda pode variar)
SELECT
  FORMAT_DATE('%G-W%V', data) AS semana,
  cliente,
  ROUND(SUM(CAST(conversoes AS FLOAT64)), 1) AS conversoes,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2) AS investimento
FROM `guara-db.guara_performance.ads_diario`
WHERE plataforma = 'google'
  AND data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY semana, cliente
ORDER BY semana DESC, cliente;
-- Monitorar se semanas antigas param de variar (sinal que janela fechou)
```

**🟢 Verificar mensalmente — tamanho das tabelas SQLite:**
```bash
# Tabelas crescem ~16.200 linhas/dia com lookback 30 dias
# (mas DELETE por período mantém só 30 dias, não acumula infinitamente)
for db in /home/ubuntu/.openclaw/workspace/agente-performance/data/performance/*/*.db; do
  echo "$db: $(du -sh $db | cut -f1)"
done
# Esperado: cada tabela gads_diario ≈ 300–500 KB (30 dias de dados)
```

---

## Arquivos relacionados

| Arquivo | Descrição |
|---|---|
| `db/bq_writer.py` | Módulo corrigido — função `_insert()` com MERGE e chave dinâmica |
| `google_ads/loader.py` | Loader corrigido — guarda por `customer.id` + DELETE por período |
| `scripts/sync_gads_diario.py` | Script corrigido — `append=False` (habilita replace por período) |
| `alteracoes/2026-10-09_bigquery-deduplicacao.md` | Registro do primeiro incidente e fix |
| `alteracoes/2026-10-13_bigquery-dedup-fix-merge-chave-plataforma.md` | Registro do segundo fix |
| `alteracoes/2026-10-13_gads-campanhas-source-of-gravadas-como-pod.md` | Registro do terceiro fix |
| `docs/ARQUITETURA_DADOS.md` | Arquitetura completa dual-write + BigQuery |

---

## 🗂️ Incidente 5 — Duplicação `shopify_diario` no BigQuery (13/10/2026)

### O que aconteceu

A tabela `shopify_diario` no BigQuery acumulou múltiplas cópias do mesmo dia: até **178 linhas para um único (cliente, data)**. Total: 19 dias duplicados entre Source OF e POD Kombucha.

Exemplo da gravidade:

| Data | Cliente | Linhas | Receita inflada |
|---|---|---|---|
| 2026-10-06 | source_of | 178 | R$ 4,7M (real: R$ 37k) |
| 2026-10-07 | pod_kombucha | 89 | R$ 1,1M (real: R$ 19k) |
| 2026-10-09 | source_of | 43 | R$ 1,2M (real: R$ 28k) |

---

### O que eu fiz

1. **Identifiquei a causa raiz** no `_insert()` do `bq_writer.py`
2. **Corrigi o método de carga** da tabela temporária: `insert_rows_json` → `load_table_from_json`
3. **Removi o filtro** `WHEN MATCHED AND S.criado_em > T.criado_em` do MERGE (era bloqueador quando mesmo timestamp)
4. **Limpei as 19 duplicatas** existentes: DELETE + reinsert do valor mais recente por (cliente, data)
5. **Atualizei o cron** `shopify-sync-horario`: `--dias 2` → `--dias 30` (cobertura retroativa, alinhado com Meta e Google)

---

### Como eu fiz

#### Diagnóstico — rastrear a origem das duplicatas

```sql
-- Detectar dias com mais de 1 linha por (cliente, data)
SELECT data, cliente, COUNT(*) as n,
  MIN(criado_em) as primeiro, MAX(criado_em) as ultimo
FROM `guara-db.guara_performance.shopify_diario`
GROUP BY data, cliente
HAVING COUNT(*) > 1
ORDER BY data DESC;
-- Resultado: 19 dias, todas as linhas com o MESMO criado_em
-- Sinal: não é múltiplos syncs diferentes — é um sync único inserindo N vezes
```

**Chave do diagnóstico:** todas as 43 linhas de 2026-10-09/source_of tinham `criado_em = 2026-10-10 23:55:10` — exatamente o mesmo timestamp. Isso significa que um único sync às 23:55 inseriu 43 cópias do mesmo dia.

#### Causa raiz — streaming buffer do BigQuery

O fluxo do MERGE era:

```
_insert() → cria tabela temp → insert_rows_json(temp) → MERGE(main ⯄ temp)
```

O problema: `insert_rows_json` usa a **Streaming API** do BigQuery. Dados inseridos via streaming ficam num buffer interno por até ~1 minuto **invisível para DML** (MERGE, DELETE, UPDATE). Durante esse buffer:

```
MERGE vê: tabela temp = vazia
         → WHEN NOT MATCHED → INSERT na principal

Sync roda de novo em 15 min:
MERGE ainda vê: temp = linha nova, principal = linha do sync anterior
              → criado_em igual → WHEN MATCHED AND S > T = FALSE → sem update
              → linha do temp não existe na principal? Mas existe...
              → BUG: streaming buffer ainda inconsistente → INSERT duplicado
```

#### Fix — `load_table_from_json` (batch síncrono)

**Antes:**
```python
# Streaming: dado no buffer ~1 min, invisível para MERGE
for i in range(0, len(rows), 500):
    errs = bq.insert_rows_json(bq.get_table(tmp_table), rows[i:i+500])
```

**Depois:**
```python
# Batch load: job síncrono, dado disponível imediatamente após job.result()
job_config = bigquery.LoadJobConfig(
    schema=schema,
    write_disposition="WRITE_APPEND",
    source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
)
load_job = bq.load_table_from_json(rows, tmp_table, job_config=job_config)
load_job.result()  # aguarda conclusão — dado garantidamente disponível
```

**Antes (MERGE clause):**
```sql
WHEN MATCHED AND S.criado_em > T.criado_em THEN UPDATE ...
-- Bloqueador: se criado_em igual (mesmo sync), nunca atualizava
```

**Depois:**
```sql
WHEN MATCHED THEN UPDATE ...
-- Sempre atualiza quando encontra match — comportamento correto
```

#### Diagrama — Streaming buffer vs batch load

```
            ANTES (insert_rows_json — streaming)

cron dispara sync
      |
      ▼
  insert_rows_json(temp)
      |     RETORNA OK imediatamente
      |     MAS dado está no streaming buffer
      |
      ▼
  MERGE(main ⯄ temp)
      |
      ▼
  BigQuery lê temp   →   temp parece VAZIA (buffer ainda não liberado)
      |
      ▼
  WHEN NOT MATCHED   →   INSERT na principal  ❌ duplicóu

  Próximo sync (15 min depois):
  buffer liberou, temp tem 1 linha, principal já tem a linha
  criado_em igual → WHEN MATCHED AND S > T = FALSE → sem update
  mas outra linha duplicada já entrou


            DEPOIS (load_table_from_json — batch)

cron dispara sync
      |
      ▼
  load_table_from_json(temp)
      |     job.result() aguarda até carga completa
      |     dado GARANTIDAMENTE disponível para DML
      |
      ▼
  MERGE(main ⯄ temp)
      |
      ▼
  BigQuery lê temp   →   temp tem exatamente as linhas esperadas
      |
      ▼
  (cliente, data) já existe   →  WHEN MATCHED → UPDATE  ✅
  (cliente, data) novo        →  WHEN NOT MATCHED → INSERT  ✅
```

#### Limpeza das 19 duplicatas

```python
# Para cada (cliente, data) duplicado:
# 1. Buscar a linha mais recente (MAX criado_em)
# 2. DELETE todas as linhas do (cliente, data)
# 3. Re-inserir via load_table_from_json (1 linha)

q_get = f"""
  SELECT * FROM shopify_diario
  WHERE cliente = '{cliente}' AND data = '{data}'
  ORDER BY criado_em DESC
  LIMIT 1
"""
correct = list(client.query(q_get).result())[0]
client.query(f"DELETE FROM shopify_diario WHERE cliente='{cliente}' AND data='{data}'").result()
client.load_table_from_json([correct_row], tabela, job_config=...).result()
# 19 dias corrigidos ✅
```

---

### Por que fiz desse jeito

**Por que `load_table_from_json` e não esperar o buffer com `time.sleep()`?**

Sleep seria frágil — o buffer pode levar de 30 segundos a vários minutos dependendo da carga do BQ. `load_table_from_json` é determinístico: o job só retorna depois que o dado está escrito e visível.

**Por que remover o `AND S.criado_em > T.criado_em` do WHEN MATCHED?**

Esse filtro foi colocado originalmente para "evitar sobrescrever com dado antigo". Na prática, o mesmo sync gera o mesmo `criado_em = datetime.now()` — quando o timestamp é idêntico, `S > T` é `FALSE`, o UPDATE é bloqueado, e o MERGE não faz nada mesmo com dado mais atualizado disponível. A proteção é desnecessária: o dado na tabela temp é sempre o mais recente (acabou de vir da API).

**Por que o `ads_diario` (Meta + Google) não sofria o mesmo bug?**

Sofria — foi exatamente o Incidente 2 (ratio 8–12×). O fix do `load_table_from_json` agora protege todas as tabelas que passam pelo `_insert()`: `ads_diario`, `shopify_diario`, e qualquer tabela futura.

---

### Consequências: custo, crescimento e monitoramento

#### Custo do incidente

| Item | Valor |
|---|---|
| Máx. linhas por dia antes do fix | 178× o valor real |
| Receita inflada no pico (2026-10-06 source_of) | R$ 4,73M (real: R$ 37k) |
| Linhas deduplicadas | 19 dias × 2 clientes |
| DELETE + reinsert no BQ | ~50 KB processados = R$ 0,00 |

#### Custo futuro com o fix

`load_table_from_json` é um **batch load job** — cobrado por dados processados na tabela destino, não por bytes da carga. Para o Shopify (7–30 linhas/sync), o impacto é zero. Mesmo com 100 clientes e 30 dias de janela (3.000 linhas/sync × 96 syncs/dia), o total processado por mês seria <1 MB — free tier coberta.

#### O que precisa ser acompanhado

**🔴 Verificar semanalmente — ratio de linhas por (cliente, data):**

```sql
-- Detectar duplicatas em shopify_diario
SELECT data, cliente, COUNT(*) AS n
FROM `guara-db.guara_performance.shopify_diario`
GROUP BY data, cliente
HAVING COUNT(*) > 1
ORDER BY n DESC;
-- Resultado esperado: 0 linhas
```

**🟡 Verificar mensalmente — receita média diária vs expected:**

```sql
-- Receita média diária dos últimos 30 dias por cliente
SELECT cliente,
  ROUND(AVG(CAST(receita AS FLOAT64)), 2) AS media_diaria,
  MAX(CAST(receita AS FLOAT64)) AS max_diario,
  MIN(CAST(receita AS FLOAT64)) AS min_diario
FROM `guara-db.guara_performance.shopify_diario`
WHERE data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY cliente;
-- Se max_diario > 5x media_diaria: possível duplicação não detectada
```

---

## 🗂️ Incidente 6 — Receita Shopify incluía frete (`total_price` → `subtotal_price`) — 13/10/2026

### O que aconteceu

A coluna `receita` da tabela `shopify_diario` usava `total_price` da API Shopify, que inclui o frete. O relatório usa vendas brutas menos desconto, sem frete (`subtotal_price`). A diferença era visível na Semana 41 da POD:

| | Valor |
|---|---|
| Receita no BQ (com frete) | R$ 143.564,42 |
| Receita no relatório (sem frete) | R$ 135.450,37 |
| Diferença | R$ 8.114,05 ← frete da semana |

Pedidos e novos clientes já batiam. Apenas a receita estava ~6% acima do esperado.

---

### O que eu fiz

1. **Confirmei a causa** buscando pedidos reais da POD via API e somando `total_price - subtotal_price`
2. **Troquei o campo** de `total_price` para `subtotal_price` como `receita` no `shopify/sync.py`
3. **Adicionei coluna `frete`** = `total_price - subtotal_price`, separada, em SQLite e BigQuery
4. **Atualizei DDL, UPSERT, bq_rows** em todos os pontos do pipeline
5. **Rodei sync retroativo 30 dias** para reprocessar o histórico com os valores corretos
6. **Validei**: Semana 41 POD → receita = R$ 135.450 ✅, frete = R$ 8.114 ✅

---

### Como eu fiz

#### Diagnóstico — confirmar origem da diferença

```python
# Buscar pedidos reais da POD e somar os campos
sh = get_client_for('pod_kombucha')
total_price_sum = subtotal_sum = frete_sum = 0

for p in sh._paginar_pedidos(date(2026,10,7), date(2026,10,13)):
    tp  = float(p.get('total_price') or 0)
    sub = float(p.get('subtotal_price') or 0)
    total_price_sum += tp
    subtotal_sum    += sub
    frete_sum       += (tp - sub)

print(f"total_price:   R$ {total_price_sum:,.2f}")  # 140.553,14
print(f"subtotal_price: R$ {subtotal_sum:,.2f}")   # 132.771,21
print(f"frete:         R$ {frete_sum:,.2f}")        # 7.781,93
# Confirma: diferença = frete. Diagnóstico encerrado.
```

#### Campos da API Shopify — o que cada um significa

| Campo | Definição | Antes | Depois |
|---|---|---|---|
| `total_price` | Subtotal + frete (valor cobrado do cliente) | ✅ usado como `receita` | ❌ |
| `subtotal_price` | Produtos após desconto, sem frete | ❌ | ✅ usado como `receita` |
| `total_discounts` | Desconto total aplicado | ✅ em `total_desconto` | ✅ inalterado |
| `total_price - subtotal_price` | Frete cobrado no pedido | ❌ embutido | ✅ coluna `frete` |

#### Fix em `shopify/sync.py` — coleta por pedido

**Antes:**
```python
preco    = float(pedido.get("total_price") or 0)   # inclui frete
desconto = float(pedido.get("total_discounts") or 0)
receita_por_dia[criado_em] += preco
```

**Depois:**
```python
total_p  = float(pedido.get("total_price") or 0)
sub_p    = float(pedido.get("subtotal_price") or 0)
desconto = float(pedido.get("total_discounts") or 0)
receita  = sub_p                        # vendas brutas - desconto, sem frete
frete    = round(total_p - sub_p, 2)   # frete isolado

receita_por_dia[criado_em] += receita
frete_por_dia[criado_em]   += frete
```

#### Fix na consolidação por dia — tuple agora com 9 campos

```python
# Antes: (data, receita, pedidos, novos, recomp, ticket, desconto, p300)
# Depois: (data, receita, frete, pedidos, novos, recomp, ticket, desconto, p300)
rows.append((dia_str, receita, frete, pedidos, novos, recomp, ticket, desconto, p300))
```

#### Fix no DDL SQLite e UPSERT

```sql
-- DDL: coluna frete adicionada
CREATE TABLE IF NOT EXISTS shopify_receita_diaria (
    ...
    receita         REAL NOT NULL DEFAULT 0,
    frete           REAL NOT NULL DEFAULT 0,   -- NOVO
    pedidos         INTEGER NOT NULL DEFAULT 0,
    ...
);

-- ALTER TABLE nas bases existentes
ALTER TABLE shopify_receita_diaria ADD COLUMN frete REAL NOT NULL DEFAULT 0;
```

#### Fix no BigQuery — 3 passos (ADD COLUMN com default não é suportado direto)

```sql
-- 1. Adicionar coluna sem default
ALTER TABLE `guara-db.guara_performance.shopify_diario`
ADD COLUMN frete NUMERIC;

-- 2. Definir default
ALTER TABLE `guara-db.guara_performance.shopify_diario`
ALTER COLUMN frete SET DEFAULT 0;

-- 3. Preencher linhas existentes
UPDATE `guara-db.guara_performance.shopify_diario`
SET frete = 0 WHERE TRUE;
```

#### Diagrama — campos Shopify e o que vai para cada coluna

```
         API Shopify (pedido individual)
         ┌─────────────────────────────────────────────┐
         │  total_price      = R$ 255.55               │
         │  subtotal_price   = R$ 219.90               │
         │  total_discounts  = R$ 0.00                 │
         └────────────┬──────────────────┬─────────────┘
                      │                  │
                      ▼                  ▼
           subtotal_price          total_price - subtotal_price
           R$ 219.90               R$ 35.65
                │                        │
                ▼                        ▼
         receita_por_dia[]        frete_por_dia[]
                │                        │
                ▼                        ▼
         SQLite: receita          SQLite: frete
         BQ: receita              BQ: frete


         total_discounts  ──────────►  SQLite: total_desconto
                                       BQ: total_desconto
```

#### Validação pós-sync (POD Kombucha, semana 41)

```sql
SELECT
  FORMAT_DATE('%G-W%V', data)      AS semana,
  SUM(CAST(receita AS FLOAT64))    AS receita_sem_frete,
  SUM(CAST(frete AS FLOAT64))      AS frete,
  SUM(CAST(receita AS FLOAT64)
    + CAST(frete AS FLOAT64))      AS total_com_frete,
  SUM(CAST(pedidos AS INTEGER))    AS pedidos
FROM `guara-db.guara_performance.shopify_diario`
WHERE cliente = 'pod_kombucha'
  AND data >= '2026-09-28'
GROUP BY semana
ORDER BY semana DESC;
```

Resultado:
```
Semana    Receita (sem frete)   Frete      Total c/frete  Pedidos
2026-W41  R$ 135.450,37         R$ 8.114   R$ 143.564     502
```
→ **R$ 135.450 bate exato com o relatório** ✅

---

### Por que fiz desse jeito

**Por que `subtotal_price` e não `total_price - total_discounts`?**

`subtotal_price` já é calculado pela Shopify como `(soma dos line_items) - total_discounts`. Não precisamos recalcular — a plataforma garante a consistência. Usar `total_price - total_discounts` seria impreciso porque `total_price` inclui frete, e o frete não tem desconto aplicado sobre ele.

**Por que criar coluna `frete` separada em vez de só trocar o campo?**

O frete é uma informação útil por si só: permite calcular ticket médio real, identificar pedidos com frete grátis, e cruzar custo de frete com margem. Perder essa informação seria um passo para trás. Separar é melhor do que embutir ou descartar.

**Por que não alterar `ticket_medio` para refletir só produtos?**

`ticket_medio` = `receita / pedidos` — com a troca de `total_price` para `subtotal_price`, o `ticket_medio` já passa a refletir o valor dos produtos sem frete automaticamente. Nenhuma mudança adicional necessária.

**Por que os 3 passos no BQ para ADD COLUMN com default?**

O BigQuery não suporta `ADD COLUMN frete NUMERIC DEFAULT 0` em tabelas existentes — exige separar em: ADD (sem default) → SET DEFAULT → UPDATE rows. É uma limitação do BQ para tabelas particionadas.

---

### Consequências: custo, crescimento e monitoramento

#### Impacto nos dados históricos

- Sync retroativo de 30 dias reprocessou ambos os clientes com os valores corretos
- Linhas anteriores a 30 dias têm `frete = 0` (valor de preenchimento do ALTER TABLE)
- Se precisar de histórico completo de frete: rodar `sync --full` (desde jan/2023 para Source OF)

#### Queries de monitoramento

**🟡 Verificar mensalmente — consistência receita vs total_price:**

```sql
-- Receita + frete deve se manter estável como proporção
-- Frete muito alto (>15% da receita) pode indicar problema
SELECT
  cliente,
  DATE_TRUNC(data, MONTH) AS mes,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2) AS receita,
  ROUND(SUM(CAST(frete AS FLOAT64)), 2) AS frete,
  ROUND(
    SUM(CAST(frete AS FLOAT64))
    / NULLIF(SUM(CAST(receita AS FLOAT64)), 0) * 100, 1
  ) AS pct_frete
FROM `guara-db.guara_performance.shopify_diario`
WHERE data >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 MONTH)
GROUP BY cliente, mes
ORDER BY mes DESC, cliente;
-- Esperado: pct_frete entre 3% e 10%
-- Se > 15%: verificar se campo receita não voltou a incluir frete por acidente
```

---

## Arquivos relacionados

| Arquivo | Descrição |
|---|---|
| `db/bq_writer.py` | `load_table_from_json` + MERGE sem filtro `criado_em` + campo `frete` em `write_shopify()` |
| `google_ads/loader.py` | Guarda por `customer.id` + DELETE por período |
| `scripts/sync_gads_diario.py` | `append=False` (replace por período) |
| `shopify/sync.py` | `subtotal_price` como receita + `frete` separado |
| `shopify/client.py` | Filtro `financial_status=paid` |
| `alteracoes/2026-10-09_bigquery-deduplicacao.md` | Registro do primeiro incidente e fix |
| `alteracoes/2026-10-13_bigquery-dedup-fix-merge-chave-plataforma.md` | Registro do segundo fix |
| `alteracoes/2026-10-13_gads-campanhas-source-of-gravadas-como-pod.md` | Registro do terceiro fix |
| `docs/ARQUITETURA_DADOS.md` | Arquitetura completa dual-write + BigQuery |

---

*Documento criado em 13/10/2026 — Cesar (Agente de Performance Guará)*
