# BigQuery — Conexão e Arquitetura

Documentação da integração BigQuery no **Agente de Performance Guará**.

---

## Visão geral

O agente usa uma arquitetura de **dual-write**: toda vez que dados são salvos no SQLite local, são espelhados automaticamente no BigQuery. O SQLite é a fonte primária (rápida, offline, logs); o BigQuery é o espelho analítico (Looker Studio, cross-cliente, histórico longo prazo).

```
Meta Ads API ──┐
Google Ads API ─┼──► loader.py ──► SQLite (primário) ──► bq_writer.py ──► BigQuery
Shopify API ───┘                                              (dual-write)
```

**Princípio:** se o BQ falhar, o sync principal **não é afetado** — a escrita no BQ é silenciosa (`try/except` que apenas loga warning).

---

## Configuração

### Variáveis de ambiente (`.env`)

```env
GOOGLE_APPLICATION_CREDENTIALS=/home/ubuntu/.openclaw/workspace/agente-performance/credentials/bq-service-account.json
BQ_PROJECT=guara-db
BQ_DATASET=guara_performance
```

### Service Account

Arquivo: `credentials/bq-service-account.json`

Permissões necessárias na service account:
- `bigquery.tables.get`
- `bigquery.tables.updateData` (streaming insert)

> ⚠️ Nunca versionar o arquivo `bq-service-account.json` no Git. Já está no `.gitignore`.

---

## Dataset e tabelas

**Projeto GCP:** `guara-db`  
**Dataset:** `guara_performance`

### Tabela `ads_diario`

Consolidada — Meta Ads + Google Ads de todos os clientes em uma única tabela.

| Campo | Tipo | Descrição |
|---|---|---|
| `cliente` | STRING | `source_of` \| `pod_kombucha` |
| `plataforma` | STRING | `meta` \| `google` |
| `data` | DATE | Data da veiculação |
| `campanha` | STRING | Nome da campanha |
| `campaign_id` | STRING | ID da campanha |
| `adset_name` | STRING | Nome do conjunto/adgroup |
| `adset_id` | STRING | ID do adset (Meta) |
| `ad_name` | STRING | Nome do anúncio |
| `ad_id` | STRING | ID do anúncio |
| `objetivo` | STRING | Objetivo da campanha |
| `status` | STRING | Status do criativo |
| `investimento` | STRING | Valor investido (R$) |
| `compras` | INTEGER | Conversões/compras |
| `receita` | STRING | Receita atribuída (R$) |
| `impressoes` | INTEGER | Impressões |
| `alcance` | INTEGER | Alcance único |
| `cliques_link` | INTEGER | Cliques no link |
| `cliques_todos` | INTEGER | Total de cliques |
| `roas` | FLOAT | ROAS calculado |
| `cpa` | FLOAT | Custo por aquisição |
| `cpm` | FLOAT | Custo por mil impressões |
| `frequencia` | FLOAT | Frequência média |
| `video_3s` | INTEGER | Views 3s (Meta) |
| `video_50pct` | INTEGER | Views 50% (Meta) |
| `thruplays` | INTEGER | ThruPlays (Meta) |
| `adicoes_carrinho` | INTEGER | Add-to-cart |
| `checkout_iniciado` | INTEGER | Checkouts iniciados |
| `visualizacoes_landing` | INTEGER | Views de landing page |
| `qualidade_ranking` | STRING | Ranking de qualidade (Meta) |
| `engagement_ranking` | STRING | Ranking de engajamento (Meta) |
| `conversion_ranking` | STRING | Ranking de conversão (Meta) |
| `criado_em` | TIMESTAMP | Timestamp de inserção no BQ |

### Tabela `shopify_diario`

Receita diária da Shopify por cliente.

| Campo | Tipo | Descrição |
|---|---|---|
| `cliente` | STRING | `source_of` \| `pod_kombucha` |
| `data` | DATE | Data |
| `receita` | STRING | Receita total do dia (R$) |
| `pedidos` | INTEGER | Número de pedidos |
| `novos_clientes` | INTEGER | Clientes com 1ª compra |
| `recompradores` | INTEGER | Clientes com 2ª+ compra |
| `ticket_medio` | STRING | Ticket médio (R$) |
| `total_desconto` | STRING | Total de desconto aplicado (R$) |
| `pedidos_acima_300` | INTEGER | Pedidos com valor > R$300 |
| `criado_em` | TIMESTAMP | Timestamp de inserção no BQ |

---

## Como funciona o dual-write

### Módulo principal: `db/bq_writer.py`

```python
# Verifica se BQ está configurado (service account presente)
if bq_writer.enabled():
    bq_writer.write_ads(rows, cliente="source_of", plataforma="meta")
```

Internamente:
1. Monta os dicts no formato das tabelas BQ
2. Filtra linhas com `data=None`
3. Chama `bq.insert_rows_json()` em batches de 500
4. Loga sucesso/erro — nunca levanta exceção para o caller

### Fluxo Meta Ads

```
meta_ads/loader.py
  └── sync() salva no SQLite
  └── bq_writer.write_ads(rows, "source_of", "meta")
        └── _insert("ads_diario", bq_rows, "source_of/meta")
```

### Fluxo Google Ads

```
google_ads/loader.py
  └── sync() salva no SQLite
  └── bq_writer.write_ads(rows, "source_of", "google")
        └── _insert("ads_diario", bq_rows, "source_of/google")
```

### Fluxo Shopify

```
db/shopify_sync.py
  └── sync() salva na tabela shopify_receita_diaria
  └── bq_writer.write_shopify(rows, "source_of")
        └── _insert("shopify_diario", bq_rows, "shopify/source_of")
```

---

## Migração histórica

Script: `bq_migrate.py`

Roda **uma vez** para popular o histórico completo do SQLite no BQ.

```bash
cd /home/ubuntu/.openclaw/workspace/agente-performance
source venv/bin/activate
python bq_migrate.py
```

Saída esperada:
```
═══════════════════════════════════════════════════════
  MIGRAÇÃO SQLite → BigQuery
  Projeto: guara-db | Dataset: guara_performance
═══════════════════════════════════════════════════════
  ✅ Meta Ads Source OF — 3.842 linhas inseridas
  ✅ Meta Ads POD Kombucha — 1.204 linhas inseridas
  ✅ Google Ads Source OF — 892 linhas inseridas
  ✅ Google Ads POD Kombucha — 0 linhas inseridas
  ✅ Shopify Source OF — 387 linhas inseridas
═══════════════════════════════════════════════════════
  Concluído em 12.4s
═══════════════════════════════════════════════════════
```

> O script usa `insert_rows_json` que é **append-only** — não faz UPSERT. Se rodado mais de uma vez, vai duplicar. Para re-migrar limpe as tabelas antes via console GCP.

---

## Verificar se BQ está ativo

```python
from db.bq_writer import enabled
print(enabled())  # True se credentials/bq-service-account.json existe
```

Ou via shell:
```bash
python -c "from db.bq_writer import enabled; print('BQ ativo:', enabled())"
```

---

## Queries úteis no BigQuery

```sql
-- Investimento e ROAS dos últimos 30 dias — Source OF
SELECT
  data,
  SUM(CAST(investimento AS FLOAT64)) AS investimento,
  SUM(compras) AS compras,
  SUM(CAST(receita AS FLOAT64)) AS receita,
  ROUND(SUM(CAST(receita AS FLOAT64)) / NULLIF(SUM(CAST(investimento AS FLOAT64)), 0), 2) AS roas
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY data
ORDER BY data DESC;

-- Receita Shopify mensal
SELECT
  FORMAT_DATE('%Y-%m', data) AS mes,
  SUM(CAST(receita AS FLOAT64)) AS receita_total,
  SUM(pedidos) AS pedidos,
  ROUND(SUM(CAST(receita AS FLOAT64)) / SUM(pedidos), 2) AS ticket_medio
FROM `guara-db.guara_performance.shopify_diario`
WHERE cliente = 'source_of'
GROUP BY mes
ORDER BY mes DESC;

-- Cross-plataforma: Meta vs Google
SELECT
  plataforma,
  SUM(CAST(investimento AS FLOAT64)) AS investimento,
  SUM(compras) AS compras,
  ROUND(SUM(CAST(receita AS FLOAT64)) / NULLIF(SUM(CAST(investimento AS FLOAT64)), 0), 2) AS roas
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
GROUP BY plataforma;
```

---

## Estrutura de arquivos

```
agente-performance/
├── credentials/
│   └── bq-service-account.json     ← service account GCP (não versionar)
├── db/
│   └── bq_writer.py                ← módulo de dual-write
├── meta_ads/
│   └── loader.py                   ← chama write_ads() após sync SQLite
├── google_ads/
│   └── loader.py                   ← chama write_ads() após sync SQLite
├── db/
│   └── shopify_sync.py             ← chama write_shopify() após sync
└── bq_migrate.py                   ← migração histórica (rodar 1x)
```

---

## Troubleshooting

| Sintoma | Causa provável | Solução |
|---|---|---|
| `BQ ativo: False` | `bq-service-account.json` não existe | Colocar o arquivo em `credentials/` |
| `[BQ] falha silenciosa: 403` | Permissão insuficiente na service account | Adicionar role `BigQuery Data Editor` no GCP |
| `[BQ] falha silenciosa: 404` | Tabela não existe no dataset | Criar as tabelas via console GCP ou schema SQL |
| Dados duplicados no BQ | `bq_migrate.py` rodado mais de uma vez | Limpar a tabela no BQ antes de re-migrar |
| Sync SQLite ok mas BQ vazio | `enabled()` retorna False | Verificar path do `GOOGLE_APPLICATION_CREDENTIALS` |
