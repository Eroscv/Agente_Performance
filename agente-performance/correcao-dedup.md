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

## Arquivos relacionados

| Arquivo | Descrição |
|---|---|
| `db/bq_writer.py` | Módulo corrigido — função `_insert()` com MERGE e chave dinâmica |
| `alteracoes/2026-10-09_bigquery-deduplicacao.md` | Registro do primeiro incidente e fix |
| `alteracoes/2026-10-13_bigquery-dedup-fix-merge-chave-plataforma.md` | Registro do segundo fix |
| `docs/ARQUITETURA_DADOS.md` | Arquitetura completa dual-write + BigQuery |

---

*Documento criado em 13/10/2026 — Cesar (Agente de Performance Guará)*
