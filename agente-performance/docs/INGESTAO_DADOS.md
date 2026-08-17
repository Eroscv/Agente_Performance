# 📡 Ingestão de Dados — Meta Ads & Google Ads

> Documentação técnica de como os dados de anúncios são coletados, transformados e armazenados no banco SQLite local.

---

## Visão Geral

A ingestão elimina a dependência de planilhas Google Sheets. Os dados são puxados **diretamente das APIs oficiais** de Meta Ads e Google Ads, transformados e armazenados em tabelas SQLite separadas por cliente.

```
Meta Ads API  ──► meta_ads/loader.py  ──► SQLite (source_of_meta_ads_diario)
Google Ads API ──► google_ads/loader.py ──► SQLite (source_of_gads_diario)
Shopify API   ──► db/shopify_sync.py  ──► SQLite (shopify_receita_diaria)
```

---

## 🏗️ Estrutura de Módulos

```
agente-performance/
├── meta_ads/
│   ├── __init__.py
│   └── loader.py          # Ingestão Meta Ads API → SQLite
│
├── google_ads/
│   ├── __init__.py
│   ├── client.py          # Autenticação Google Ads API
│   └── loader.py          # Ingestão Google Ads API → SQLite
│
├── scripts/
│   ├── sync_meta_diario.py   # Script de sync diário Meta Ads
│   └── sync_gads_diario.py   # Script de sync diário Google Ads
│
└── clientes.json             # Registry de clientes e contas
```

---

## 📋 Clientes e Contas Configuradas

Toda a configuração de clientes fica em `clientes.json`. Para adicionar um novo cliente, basta adicionar um bloco — **sem mexer em código**.

```json
{
  "source_of": {
    "nome": "Source OF",
    "ativo": true,
    "plataformas": {
      "meta_ads": {
        "ativo": true,
        "meta_account_id": "act_1059884611898142",
        "tabela_db": "source_of_meta_ads_diario",
        "meta_token_env": "META_ACCESS_TOKEN"
      },
      "google_ads": {
        "ativo": true,
        "gads_customer_id": "4311260590",
        "tabela_db": "source_of_gads_diario"
      }
    }
  },
  "pod_kombucha": {
    "nome": "POD Kombucha",
    "ativo": true,
    "plataformas": {
      "meta_ads": { "ativo": true, "meta_account_id": "act_XXXXXXX" },
      "google_ads": { "ativo": false, "_comentario": "aguardando customer_id" }
    }
  }
}
```

### Padrão de nomenclatura das tabelas
```
{cliente_key}_{plataforma}_diario
```
Exemplo: `source_of_meta_ads_diario`, `source_of_gads_diario`

---

## 🔵 Meta Ads — Configuração

### Autenticação

O loader usa **token de longa duração (Long-Lived Token)** gerado manualmente pelo Meta Business Manager. Não usa OAuth flow automático.

**Variável de ambiente:**
```env
META_ACCESS_TOKEN=EAAxxxxx...   # Token permanente (~60 dias)
```

Para múltiplos clientes com contas diferentes, cada cliente pode ter seu próprio token:
```json
"meta_token_env": "META_ACCESS_TOKEN_CLIENTE_X"
```

### Como funciona a coleta

**Módulo:** `meta_ads/loader.py`

```
Meta Ads API (Graph API v20.0)
    └── /{account_id}/insights
         ├── level: ad          (granularidade por anúncio)
         ├── time_increment: 1  (um registro por dia)
         ├── filtering: spend > 0  (só anúncios com gasto)
         └── Paginação automática via cursor
```

#### Janela de coleta — lotes de 14 dias

A API do Meta tem limite de paginação recomendado. O loader divide períodos longos em **lotes de 14 dias**, com pausa de 1s entre lotes:

```
dias=30 → Lote 1 (dia 1-14) + Lote 2 (dia 15-28) + Lote 3 (dia 29-30)
```

**Rate limiting:** Se receber HTTP 429, aguarda 60s e tenta novamente.

#### Campos coletados

| Campo API | Campo SQLite | Descrição |
|---|---|---|
| `campaign_name` | `campanha` | Nome da campanha |
| `campaign_id` | `campaign_id` | ID da campanha |
| `adset_name` | `adset_name` | Nome do conjunto de anúncios |
| `ad_name` / `ad_id` | `ad_name` / `ad_id` | Anúncio específico |
| `date_start` | `data` | Data (YYYY-MM-DD) |
| `spend` | `investimento` | Gasto em BRL |
| `impressions` | `impressoes` | Impressões |
| `reach` | `alcance` | Alcance único |
| `clicks` | `cliques_link` | Cliques no link |
| `cpm` | `cpm` | Custo por mil impressões |
| `frequency` | `frequencia` | Frequência média |
| `actions[purchase]` | `compras` | Número de compras |
| `action_values[purchase]` | `valor_compras` | Receita atribuída |
| `actions[add_to_cart]` | `adicoes_carrinho` | Adições ao carrinho |
| `actions[initiate_checkout]` | `checkout_iniciado` | Checkouts iniciados |
| `video_thruplay_watched_actions` | `thruplays` | Thruplays de vídeo |

#### Métricas calculadas internamente

| Métrica | Fórmula |
|---|---|
| `roas_compras` | `valor_compras / investimento` |
| `cpa_meta` | `investimento / compras` |

> **ROAS real** (usado nos relatórios): `Receita Shopify ÷ (Investimento Meta × 1,1215)` — o fator `1.1215` corrige o imposto sobre investimento em mídia.

#### Estratégia de upsert

```python
# Ao salvar: deleta apenas o período sendo recarregado (não toda a tabela)
DELETE FROM {tabela} WHERE data BETWEEN ? AND ?
# Depois insere todos os registros do período
INSERT OR REPLACE INTO {tabela} ...
```

Isso garante que dados de outros períodos não sejam perdidos em caso de sync parcial.

### Uso manual

```bash
source venv/bin/activate

# Sync dos últimos 8 dias (padrão cron)
python scripts/sync_meta_diario.py

# Sync de 30 dias para um cliente específico
python scripts/sync_meta_diario.py --dias 30 --cliente source_of

# Dry run (mostra o que buscaria, sem salvar)
python -m meta_ads.loader --account-id act_1059884611898142 --dry-run
```

---

## 🟢 Google Ads — Configuração

### Autenticação

Usa o **SDK oficial** `google-ads` com credenciais OAuth2 (refresh token permanente).

**Variáveis de ambiente:**
```env
GOOGLE_ADS_DEVELOPER_TOKEN=      # Token de desenvolvedor (Google Ads API Center)
GOOGLE_ADS_CLIENT_ID=            # OAuth2 Client ID (Google Cloud Console)
GOOGLE_ADS_CLIENT_SECRET=        # OAuth2 Client Secret
GOOGLE_ADS_REFRESH_TOKEN=        # Refresh token permanente
GOOGLE_ADS_LOGIN_CUSTOMER_ID=    # MCC (manager account) customer ID
```

O cliente é inicializado em `google_ads/client.py`, que monta o YAML de configuração dinamicamente a partir das variáveis de ambiente:

```python
def get_client() -> GoogleAdsClient:
    yaml_str = _YAML_TEMPLATE.format(
        developer_token=os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
        ...
    )
    return GoogleAdsClient.load_from_string(yaml_str)
```

### Como funciona a coleta

**Módulo:** `google_ads/loader.py`

Usa **GAQL (Google Ads Query Language)** — SQL-like para a API do Google Ads:

```sql
SELECT
    campaign.id,
    campaign.name,
    campaign.advertising_channel_type,
    campaign.status,
    segments.date,
    metrics.impressions,
    metrics.clicks,
    metrics.cost_micros,
    metrics.conversions,
    metrics.conversions_value,
    metrics.ctr,
    metrics.average_cpc
FROM campaign
WHERE segments.date BETWEEN '{date_start}' AND '{date_end}'
  AND campaign.status != 'REMOVED'
ORDER BY segments.date DESC, metrics.cost_micros DESC
```

#### Conversão de moeda

O Google Ads retorna custos em **micros** (1 BRL = 1.000.000 micros):

```python
def _micros_to_brl(micros: int) -> float:
    return round(micros / 1_000_000, 2)
```

#### Campos coletados

| Campo API | Campo SQLite | Descrição |
|---|---|---|
| `campaign.name` | `campanha` | Nome da campanha |
| `campaign.id` | `campaign_id` | ID da campanha |
| `campaign.advertising_channel_type` | `tipo_campanha` | SEARCH, DISPLAY, etc. |
| `campaign.status` | `status` | ENABLED, PAUSED, etc. |
| `segments.date` | `data` | Data (YYYY-MM-DD) |
| `metrics.cost_micros` | `investimento` | Gasto em BRL |
| `metrics.impressions` | `impressoes` | Impressões |
| `metrics.clicks` | `cliques` | Cliques |
| `metrics.conversions` | `conversoes` | Conversões |
| `metrics.conversions_value` | `valor_conversao` | Valor de conversão |
| `metrics.ctr` | `ctr` | CTR em % |
| `metrics.average_cpc` | `cpc_medio` | CPC médio em BRL |

#### Estratégia de upsert

```python
# Google Ads: limpa toda a tabela e reinseree
# (dados históricos são estáveis, sem atribuição retroativa)
DELETE FROM {tabela}
INSERT OR REPLACE INTO {tabela} ...
```

### Uso manual

```bash
source venv/bin/activate

# Sync dos últimos 8 dias
python scripts/sync_gads_diario.py

# Sync de 30 dias para um cliente específico
python scripts/sync_gads_diario.py --dias 30 --cliente source_of

# Dry run
python -m google_ads.loader --customer-id 4311260590 --dry-run
```

---

## 🛍️ Shopify — Configuração

### Autenticação

```env
SHOPIFY_STORE=minha-loja       # Subdomínio Shopify
SHOPIFY_TOKEN=shpat_xxxxx      # Admin API Token
```

### Como funciona

O `db/shopify_sync.py` puxa pedidos via REST Admin API e agrega por dia:
- Endpoint: `GET /admin/api/2024-01/orders.json`
- Paginação por cursor
- Timeout: **5s por requisição, 1 tentativa** (sem retry — proteção contra travamento)
- Resultado armazenado em `shopify_receita_diaria`

### Dados sincronizados

- **387 dias** de histórico
- **31.285 pedidos**
- **R$ 9,58M** em receita total

---

## ⏰ Crons de Ingestão

Todos os crons são gerenciados pelo OpenClaw e rodam em sessões isoladas.

| Nome | Horário (UTC) | Comando | Timeout |
|---|---|---|---|
| `shopify-sync-diario` | 06h00 diário | `scripts/cron_shopify_sync.py --dias 8` | 300s |
| `gads-sync-diario` | 07h00 diário | `scripts/sync_gads_diario.py --dias 8` | 120s |
| `meta-sync-diario` | 07h30 diário | `scripts/sync_meta_diario.py --dias 8` | 600s |
| `relatorio-diario-performance` | 09h00 diário | `scripts/relatorio_diario.py` | 120s |
| `Relatório Diário — Todos os Clientes` | 10h00 (BRT) | `cron_todos_relatorios.py --periodo diario` | 300s |
| `Relatório Semanal — Todos os Clientes` | 11h00 seg (BRT) | `cron_todos_relatorios.py --periodo semana` | 300s |
| `Relatório Mensal — Todos os Clientes` | 11h00 dia 1 (BRT) | `cron_todos_relatorios.py --periodo mes` | 600s |

### Janela de 8 dias

Os crons sempre puxam os **últimos 8 dias** (não só ontem). Isso cobre:
- Dados com atribuição retroativa no Meta (conversões atribuídas até 7 dias depois)
- Pedidos Shopify que chegam com atraso
- Reprocessamento automático de dias com falha

---

## 📊 Schema do Banco SQLite

**Arquivo:** `data/campanhas.db`

### Tabela Meta Ads (`{cliente}_meta_ads_diario`)

```sql
CREATE TABLE source_of_meta_ads_diario (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    data                  TEXT NOT NULL,           -- YYYY-MM-DD
    campanha              TEXT,
    campaign_id           TEXT,
    adset_name            TEXT,
    adset_id              TEXT,
    ad_name               TEXT,
    ad_id                 TEXT,
    objetivo              TEXT,
    status_criativo       TEXT,
    impressoes            REAL DEFAULT 0,
    alcance               REAL DEFAULT 0,
    cliques_link          REAL DEFAULT 0,
    cliques_todos         REAL DEFAULT 0,
    investimento          REAL DEFAULT 0,
    compras               REAL DEFAULT 0,
    valor_compras         REAL DEFAULT 0,
    cpa_meta              REAL,
    roas_compras          REAL,
    cpm                   REAL,
    frequencia            REAL,
    video_3s              REAL DEFAULT 0,
    video_50pct           REAL DEFAULT 0,
    thruplays             REAL DEFAULT 0,
    adicoes_carrinho      REAL DEFAULT 0,
    checkout_iniciado     REAL DEFAULT 0,
    visualizacoes_landing REAL DEFAULT 0,
    UNIQUE(data, campanha, ad_id)
)
```

**Índices:** `idx_data`, `idx_campanha`, `idx_data_campanha`

### Tabela Google Ads (`{cliente}_gads_diario`)

```sql
CREATE TABLE source_of_gads_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data            TEXT NOT NULL,       -- YYYY-MM-DD
    campanha        TEXT,
    campaign_id     TEXT,
    tipo_campanha   TEXT,                -- SEARCH, DISPLAY, SHOPPING, etc.
    status          TEXT,                -- ENABLED, PAUSED
    impressoes      REAL DEFAULT 0,
    cliques         REAL DEFAULT 0,
    investimento    REAL DEFAULT 0,      -- em BRL (já convertido de micros)
    conversoes      REAL DEFAULT 0,
    valor_conversao REAL DEFAULT 0,
    ctr             REAL,                -- em %
    cpc_medio       REAL,
    UNIQUE(data, campaign_id)
)
```

### Views unificadas

```sql
-- v_performance: visão unificada Meta + Google por período
-- campanhas_unificada: todas as campanhas de todos os clientes
```

---

## 🔍 Validação de Dados

O `db/validator.py` roda automaticamente a cada build de relatório e emite warnings esperados:

| Warning | Causa | É erro? |
|---|---|---|
| `invest_zero_com_conversao` | Atribuição cross-campaign Meta | ❌ Normal |
| `roas_ausente_com_receita` | Meta não reportou ROAS para a linha | ❌ Normal |
| `consistencia_roas` | Janela de atribuição diferente no Meta | ❌ Normal |

---

## 🔧 Log de Execuções

Cada sync registra no banco (`db/cron_log.py` → tabela `cron_runs`):

```
status: iniciado → sucesso | erro
campos: cliente_key, periodo, data_inicio, data_fim, linhas_inseridas, erro
```

---

## 🚀 Adicionando um Novo Cliente

1. Adicionar bloco em `clientes.json` com `cliente_key`, `ativo: true`, contas e tabelas
2. Se Meta Ads: definir `META_ACCESS_TOKEN_CLIENTE_X` no `.env`
3. Se Google Ads: confirmar que o `login_customer_id` (MCC) tem acesso à conta
4. Rodar manualmente: `python scripts/sync_meta_diario.py --cliente novo_cliente --dias 90`
5. Verificar no banco: `sqlite3 data/campanhas.db "SELECT COUNT(*), MIN(data), MAX(data) FROM novo_cliente_meta_ads_diario"`

---

## 📝 Histórico da Migração

| Etapa | O que foi feito |
|---|---|
| Antes | Dados vinham via Google Sheets (exportação manual ou Windsor) |
| Fase 1 | Instalação SDKs `facebook-business` e `google-ads` |
| Fase 2 | Criação dos módulos `meta_ads/` e `google_ads/` com loaders |
| Fase 3 | Scripts `sync_meta_diario.py` e `sync_gads_diario.py` com log em SQLite |
| Fase 4 | Migração das tabelas legadas para o padrão `{cliente}_{plataforma}_diario` |
| Fase 5 | Configuração dos crons diários no OpenClaw |
| Fase 6 | Builders de relatório atualizados para ler do SQLite (sem Sheets) |
| Resultado | Ingestão 100% automática — dados frescos todo dia sem intervenção manual |
