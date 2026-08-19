# SCHEMA.md — Data Warehouse Local (DuckDB + Parquet)

**Versão:** 1.0  
**Data:** 2026-08-19  
**Status:** ✅ Aprovado — base para implementação das Etapas 2–5

---

## Visão Geral

O DW coexiste com o SQLite operacional. Cada camada tem responsabilidade exclusiva:

```
SQLite  → escrita (sync de APIs, logs, cache)         INTOCADO
Parquet → leitura analítica (histórico consolidado)   NOVO
DuckDB  → motor que lê os dois de forma transparente  JÁ EXISTE
```

O código do agente e os endpoints da FastAPI **não mudam** — eles continuam usando
as views DuckDB (`v_performance`, `campanhas_unificada`) que passam a ler do Parquet
para histórico e do SQLite para dados frescos (ontem/hoje).

---

## Estrutura de Arquivos

```
data/
  warehouse/
    SCHEMA.md                          ← este arquivo
    dim_cliente.parquet                ← ~10 linhas, quase estático
    dim_campanha.parquet               ← ~300 linhas, cresce devagar
    dim_tempo.parquet                  ← 2024-01-01 → 2030-12-31 (2557 dias)
    fato_performance/
      fato_performance_2024-01.parquet
      fato_performance_2024-02.parquet
      ...
      fato_performance_2026-08.parquet ← partição do mês atual
    fato_ads/
      fato_ads_2024-01.parquet
      ...
      fato_ads_2026-08.parquet
    fato_shopify/
      fato_shopify_2025-06.parquet
      ...
      fato_shopify_2026-08.parquet
    _etl_state.json                    ← controle de última execução do ETL
```

**Particionamento mensal** — cada arquivo cobre um mês completo.
Para reprocessar um período: deleta o arquivo do mês e roda o ETL com `--month 2026-08`.
DuckDB lê todos os arquivos de uma tabela com glob: `read_parquet('fato_performance/*.parquet')`.

---

## Tabelas Dimensão

### `dim_cliente`

Catálogo de clientes. Atualizado manualmente ou via ETL quando `clientes.json` muda.

| Coluna | Tipo | Descrição |
|---|---|---|
| `cliente_id` | VARCHAR | **PK** — chave interna (`source_of`, `pod_kombucha`) |
| `nome` | VARCHAR | Nome legível (`Source OF`, `POD Kombucha`) |
| `slack_channel` | VARCHAR | Canal Slack principal |
| `slack_emoji` | VARCHAR | Emoji do cliente |
| `tem_meta` | BOOLEAN | Tem Meta Ads ativo |
| `tem_gads` | BOOLEAN | Tem Google Ads ativo |
| `tem_shopify` | BOOLEAN | Tem Shopify integrado |
| `ativo` | BOOLEAN | Cliente ativo no sistema |
| `_etl_ts` | TIMESTAMP | Timestamp da última atualização |

**Linhas esperadas:** 2 (hoje), ~10 no futuro.

---

### `dim_campanha`

Catálogo de campanhas únicas por cliente e plataforma.
Atualizado pelo ETL — nova campanha detectada → nova linha inserida.

| Coluna | Tipo | Descrição |
|---|---|---|
| `campanha_sk` | VARCHAR | **PK** — surrogate key: `{cliente_id}__{plataforma}__{campaign_id}` |
| `campaign_id` | VARCHAR | ID original da plataforma (NULL em campanhas legadas) |
| `campanha` | VARCHAR | Nome da campanha |
| `cliente_id` | VARCHAR | FK → `dim_cliente.cliente_id` |
| `plataforma` | VARCHAR | `meta_ads` \| `google_ads` |
| `objetivo` | VARCHAR | Objetivo normalizado (ver enum abaixo) |
| `tipo_campanha` | VARCHAR | Tipo específico por plataforma |
| `primeira_data` | DATE | Primeira data com dados |
| `ultima_data` | DATE | Última data com dados |
| `ativa` | BOOLEAN | TRUE se teve dados nos últimos 30 dias |
| `_etl_ts` | TIMESTAMP | Timestamp da última atualização |

**Enum `objetivo` normalizado:**

| Valor no banco | Normalizado | Plataforma |
|---|---|---|
| `OUTCOME_SALES` | `conversao` | Meta Ads |
| `OUTCOME_AWARENESS` | `awareness` | Meta Ads |
| `OUTCOME_ENGAGEMENT` | `engajamento` | Meta Ads |
| `LINK_CLICKS` | `trafego` | Meta Ads |
| `SEARCH` / `Search` | `search` | Google Ads |
| `SHOPPING` / `Shopping` | `shopping` | Google Ads |
| `PERFORMANCE_MAX` | `pmax` | Google Ads |
| `DEMAND_GEN` | `demand_gen` | Google Ads |
| `NULL` | `desconhecido` | Ambos |

**Linhas esperadas:** ~300 (hoje), cresce ~5/mês conforme novos anúncios.

---

### `dim_tempo`

Calendário pré-gerado. Permite filtros e agrupamentos temporais sem cálculos nas queries analíticas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `data` | DATE | **PK** |
| `ano` | INT | Ex: 2026 |
| `mes` | INT | 1–12 |
| `mes_nome` | VARCHAR | `Janeiro` … `Dezembro` |
| `trimestre` | INT | 1–4 |
| `semana_iso` | INT | Semana ISO (1–53) |
| `dia_mes` | INT | 1–31 |
| `dia_semana` | INT | 1=Segunda … 7=Domingo |
| `dia_semana_nome` | VARCHAR | `Segunda` … `Domingo` |
| `fim_de_semana` | BOOLEAN | TRUE para Sábado e Domingo |
| `ano_mes` | VARCHAR | `2026-08` (útil para GROUP BY mensal) |
| `ano_semana` | VARCHAR | `2026-W33` (útil para GROUP BY semanal) |

**Linhas:** 2557 (2024-01-01 → 2030-12-31). Gerado uma vez, nunca muda.

---

## Tabelas Fato

### `fato_performance` — nível campanha

**Granularidade:** 1 linha por `(data, campanha_sk, plataforma)`.  
**Fonte:** Agrega todas as linhas do SQLite bruto para aquele dia + campanha (soma de todos os ad sets e anúncios).  
**Google Ads:** já vem 1:1 (sem agregação necessária).

| Coluna | Tipo | Descrição |
|---|---|---|
| `data` | DATE | **PK parcial** — data do dado |
| `campanha_sk` | VARCHAR | **PK parcial** — FK → `dim_campanha.campanha_sk` |
| `cliente_id` | VARCHAR | FK → `dim_cliente.cliente_id` |
| `plataforma` | VARCHAR | `meta_ads` \| `google_ads` |
| `investimento` | DOUBLE | Total investido no dia (R$) |
| `receita` | DOUBLE | Receita atribuída no dia (R$) |
| `impressoes` | DOUBLE | Total de impressões |
| `cliques` | DOUBLE | Total de cliques no link |
| `alcance` | DOUBLE | Pessoas únicas alcançadas |
| `conversoes` | DOUBLE | Compras / conversões |
| `video_3s` | DOUBLE | Visualizações 3s (Meta only, NULL para GAdS) |
| `thruplays` | DOUBLE | ThruPlays (Meta only, NULL para GAdS) |
| `roas` | DOUBLE | `receita / investimento` (calculado no ETL) |
| `cpa` | DOUBLE | `investimento / conversoes` (calculado no ETL) |
| `ctr` | DOUBLE | `cliques / impressoes * 100` (calculado no ETL) |
| `cpc_medio` | DOUBLE | `investimento / cliques` (calculado no ETL) |
| `_etl_ts` | TIMESTAMP | Quando esta linha foi gravada no DW |
| `_source_db` | VARCHAR | Arquivo SQLite de origem (para rastreabilidade) |

**Chave primária composta:** `(data, campanha_sk)`  
**Volume estimado:**  
- Hoje: ~36.000 linhas (equivalente ao SQLite agregado)  
- Crescimento: ~50 linhas/dia (~18.000/ano)  
- Em 5 anos: ~125.000 linhas — trivial para DuckDB

---

### `fato_ads` — nível anúncio (Meta Ads only)

**Granularidade:** 1 linha por `(data, ad_id)`.  
**Fonte:** Direto do SQLite bruto sem agregação — preserva criativo individual.  
**Google Ads:** não tem este nível de detalhe, não entra nesta tabela.

| Coluna | Tipo | Descrição |
|---|---|---|
| `data` | DATE | **PK parcial** |
| `ad_id` | VARCHAR | **PK parcial** — ID do anúncio na Meta |
| `campanha_sk` | VARCHAR | FK → `dim_campanha.campanha_sk` |
| `cliente_id` | VARCHAR | FK → `dim_cliente.cliente_id` |
| `campaign_id` | VARCHAR | ID da campanha pai |
| `adset_id` | VARCHAR | ID do conjunto de anúncios |
| `adset_name` | VARCHAR | Nome do conjunto |
| `ad_name` | VARCHAR | Nome do anúncio (criativo) |
| `investimento` | DOUBLE | Investimento do anúncio no dia |
| `receita` | DOUBLE | Receita atribuída ao anúncio |
| `impressoes` | DOUBLE | |
| `cliques` | DOUBLE | |
| `alcance` | DOUBLE | |
| `conversoes` | DOUBLE | |
| `video_3s` | DOUBLE | |
| `thruplays` | DOUBLE | |
| `adicoes_carrinho` | DOUBLE | |
| `checkout_iniciado` | DOUBLE | |
| `roas` | DOUBLE | Calculado no ETL |
| `cpa` | DOUBLE | Calculado no ETL |
| `ctr` | DOUBLE | Calculado no ETL |
| `qualidade_ranking` | VARCHAR | `ABOVE_AVERAGE`, `AVERAGE`, `BELOW_AVERAGE`, `UNKNOWN` |
| `engagement_ranking` | VARCHAR | Idem |
| `conversion_ranking` | VARCHAR | Idem |
| `_etl_ts` | TIMESTAMP | |
| `_source_db` | VARCHAR | |

**Chave primária composta:** `(data, ad_id)`  
**Volume estimado:**  
- Hoje: ~32.000 linhas  
- Crescimento: ~500 linhas/dia (~180.000/ano)  
- Em 5 anos: ~950.000 linhas — ainda confortável para DuckDB

---

### `fato_shopify` — receita diária

**Granularidade:** 1 linha por `(data, cliente_id)`.  
**Fonte:** `shopify_receita.db` da source_of.

| Coluna | Tipo | Descrição |
|---|---|---|
| `data` | DATE | **PK parcial** |
| `cliente_id` | VARCHAR | **PK parcial** — FK → `dim_cliente.cliente_id` |
| `receita` | DOUBLE | Receita total do dia (R$) |
| `pedidos` | INT | Número de pedidos |
| `novos_clientes` | INT | Pedidos de clientes novos |
| `recompradores` | INT | Pedidos de clientes recorrentes |
| `ticket_medio` | DOUBLE | `receita / pedidos` |
| `total_desconto` | DOUBLE | Total de descontos aplicados |
| `pedidos_acima_300` | INT | Pedidos com ticket > R$ 300 |
| `taxa_recompra` | DOUBLE | `recompradores / pedidos * 100` (calculado no ETL) |
| `_etl_ts` | TIMESTAMP | |
| `_source_db` | VARCHAR | |

**Volume estimado:**  
- Hoje: 414 linhas (junho/2025 → agosto/2026)  
- Crescimento: ~1 linha/dia (~365/ano)

---

## Decisões de Design

### 1. Métricas calculadas no ETL, não nas queries
ROAS, CPA, CTR e CPC são calculados uma vez na gravação do Parquet.
Queries de leitura usam diretamente — sem recalcular a cada consulta.

### 2. Particionamento mensal
Um arquivo Parquet por mês por tabela fato.
- Reprocessar um mês = deletar + regravar apenas 1 arquivo
- DuckDB lê com glob: `read_parquet('fato_performance/*.parquet')`
- Compressão ZSTD — ~5x menor que CSV, leitura ~10x mais rápida que SQLite para analytics

### 3. Dados frescos continuam no SQLite
O ETL roda às 07:00 BRT com dados de até ontem.
Dados de hoje ficam no SQLite e são lidos pelo DuckDB via ATTACH (já funciona hoje).
A view híbrida na Etapa 3 une Parquet (histórico) + SQLite (dados frescos) de forma transparente.

### 4. Surrogate key em `dim_campanha`
`campaign_id` é NULL em dados legados (antes de ~maio/2026 no source_of Meta).
A `campanha_sk` garante chave única mesmo sem `campaign_id`:
`{cliente_id}__{plataforma}__{campaign_id OR slug(campanha)}`

### 5. `fato_ads` só Meta Ads
Google Ads já entrega dados agregados por campanha na API — não há nível de anúncio disponível.
`fato_ads` terá somente linhas `plataforma = 'meta_ads'`. Documentado para evitar confusão.

### 6. Sem SCD (Slowly Changing Dimensions) por ora
`dim_campanha` e `dim_cliente` são sobrescritos a cada ETL.
Histórico de mudanças de nome de campanha não é rastreado — não há demanda hoje.
Se necessário no futuro, adicionar colunas `valid_from` / `valid_to`.

---

## Views DuckDB (Etapa 3)

As views a seguir serão criadas em `db/duck.py` para expor o DW ao restante do código:

```sql
-- Histórico completo via Parquet (rápido para analytics)
CREATE VIEW dw_performance AS
SELECT * FROM read_parquet('data/warehouse/fato_performance/*.parquet');

-- Híbrida: Parquet para histórico + SQLite para dados frescos
CREATE VIEW v_performance_full AS
    SELECT * FROM dw_performance
    WHERE data < CURRENT_DATE - INTERVAL '1' DAY
UNION ALL
    SELECT * FROM campanhas_unificada  -- SQLite via ATTACH (já existe)
    WHERE data >= CURRENT_DATE - INTERVAL '1' DAY;
```

As views `v_performance` e `campanhas_unificada` **não mudam** — compatibilidade total.

---

## Controle de ETL

Arquivo `data/warehouse/_etl_state.json` — atualizado a cada execução do ETL:

```json
{
  "ultimo_run": "2026-08-19T07:00:00",
  "ultimo_run_status": "ok",
  "ultimo_dia_processado": "2026-08-18",
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

## Checklist de Validação (Etapa 2)

Antes de declarar o ETL pronto, validar:

- [ ] `SUM(investimento)` no Parquet == `SUM(investimento)` no SQLite por cliente/mês
- [ ] `SUM(receita)` no Parquet == `SUM(receita)` no SQLite por cliente/mês
- [ ] Zero linhas duplicadas em `fato_performance` (chave `data + campanha_sk`)
- [ ] Zero linhas duplicadas em `fato_ads` (chave `data + ad_id`)
- [ ] Todos os dias do range estão presentes (sem gaps não esperados)
- [ ] `dim_campanha` cobre 100% das `campanha_sk` presentes em `fato_performance`
- [ ] `dim_tempo` cobre todo o range de datas dos fatos
- [ ] ETL incremental não duplica linhas ao rodar duas vezes no mesmo dia
