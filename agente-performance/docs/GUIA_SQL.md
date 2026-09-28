# 📖 Guia de SQL — Agente de Performance Guará

> Referência técnica para consultas no banco de dados do Agente.  
> Gerado por Cesar ⚙️ — atualizado em 28/09/2026

---

## 1. Arquitetura de Dados

### Fontes primárias (SQLite)

```
data/performance/
├── source_of/
│   ├── meta_ads.db        → tabela: source_of_meta_ads_diario
│   ├── google_ads.db      → tabela: source_of_gads_diario
│   └── shopify_receita.db → tabela: shopify_receita_diaria
└── pod_kombucha/
    ├── meta_ads.db        → tabela: pod_kombucha_meta_ads_diario
    └── shopify_receita.db → tabela: shopify_receita_diaria
```

### Camada federada (DuckDB — leitura)

O `db/connections.py` anexa todos os bancos em memória via DuckDB com schemas:

| Schema attach | Conteúdo |
|---|---|
| `sof_meta` | Meta Ads Source OF |
| `pod_meta` | Meta Ads POD Kombucha |
| `sof_gads` | Google Ads Source OF |
| `sof_shopify` | Shopify Source OF |
| `pod_shopify` | Shopify POD Kombucha |

### View canônica principal

```sql
VIEW: campanhas_unificada
```

**Use esta view para qualquer consulta de performance.** Ela unifica Meta Ads e Google Ads de todos os clientes em uma estrutura flat.

### Espelho analítico (BigQuery)

Dataset: `guara_performance`

| Tabela BQ | Origem |
|---|---|
| `ads_diario` | Espelho de Meta + Google Ads (dual-write) |
| `shopify_diario` | Espelho de receita Shopify |
| `v_performance_mensal` | View analítica — ROAS real, MoM, YoY |

---

## 2. Schema — `campanhas_unificada`

| Coluna | Tipo | Descrição |
|---|---|---|
| `data_inicio` | TEXT | Data início do período (`YYYY-MM-DD`) |
| `data_fim` | TEXT | Data fim do período (`YYYY-MM-DD`) |
| `campanha` | TEXT | Nome da campanha |
| `cliente_key` | TEXT | `'source_of'` ou `'pod_kombucha'` |
| `cliente_nome` | TEXT | Nome legível do cliente |
| `plataforma` | TEXT | `'meta_ads'` ou `'google_ads'` |
| `investimento` | REAL | Gasto total em R$ |
| `valor_compras` | REAL | Receita gerada em R$ |
| `compras` | REAL | Número de compras/conversões |
| `impressoes` | REAL | Impressões totais |
| `cliques_link` | REAL | Cliques no link |
| `cliques_todos` | REAL | Todos os cliques (inclusive engajamento) |
| `alcance` | REAL | Pessoas únicas alcançadas |
| `frequencia` | REAL | `impressoes / alcance` |
| `cpm` | REAL | Custo por mil impressões |
| `ctr_link` | REAL | Taxa de clique no link |
| `cpc_link` | REAL | Custo por clique no link |
| `cpa` | REAL | Custo por aquisição |
| `roas_compras` | REAL | ROAS bruto da plataforma |
| `video_3s` | REAL | Visualizações de vídeo ≥ 3s |
| `thruplays` | REAL | Visualizações completas |
| `reproducoes_video` | REAL | Reproduções iniciadas |
| `visualizacoes_landing` | REAL | Views da landing page |
| `adicoes_carrinho` | REAL | Adições ao carrinho |
| `checkout_iniciado` | REAL | Checkouts iniciados |
| `compras_app` | REAL | Compras via app |
| `compras_site` | REAL | Compras via site |
| `conversas_iniciadas` | REAL | Conversas WhatsApp/Messenger |

---

## 3. Regras Absolutas

### ⚠️ 3.1 — Sempre filtrar por `cliente_key`

Todo SELECT **obrigatoriamente** deve ter `WHERE cliente_key = '...'`. Sem isso, os dados de clientes diferentes se misturam e os resultados ficam errados.

```sql
-- ✅ Correto
SELECT campanha, SUM(investimento) AS invest
FROM campanhas_unificada
WHERE cliente_key = 'source_of'
  AND data_inicio >= '2026-09-01'
GROUP BY campanha;

-- ❌ Errado — mistura Source OF e POD Kombucha
SELECT campanha, SUM(investimento) AS invest
FROM campanhas_unificada
GROUP BY campanha;
```

### ⚠️ 3.2 — ROAS sempre ponderado, nunca AVG

```sql
-- ✅ Correto — ROAS ponderado
ROUND(SUM(valor_compras) / NULLIF(SUM(investimento), 0), 2) AS roas

-- ❌ Errado — distorce o resultado (média simples não é correta)
AVG(roas_compras) AS roas
```

### ⚠️ 3.3 — Sempre usar NULLIF para evitar divisão por zero

```sql
-- ✅ Correto
ROUND(SUM(investimento) / NULLIF(SUM(compras), 0), 2) AS cpa

-- ❌ Errado — vai quebrar quando compras = 0
SUM(investimento) / SUM(compras) AS cpa
```

### ⚠️ 3.4 — Datas sempre em formato ISO

```sql
-- ✅ Correto
WHERE data_inicio >= '2026-09-01'

-- ❌ Errado
WHERE data_inicio >= '01/09/2026'
```

### ⚠️ 3.5 — LIMIT obrigatório (máximo 50)

Toda query deve ter `LIMIT`. O validador injeta `LIMIT 50` automaticamente se ausente, mas é boa prática incluir explicitamente.

---

## 4. Fator de Ajuste Meta Ads

O Meta Ads **não inclui impostos** no valor reportado. Para calcular CPA real e ROAS real, aplicar fator `1.1215`:

```sql
-- CPA ajustado (com impostos)
ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(compras), 0), 2) AS cpa_ajustado

-- ROAS real (receita Shopify ÷ investimento com impostos)
ROUND(SUM(valor_shopify) / NULLIF(SUM(investimento) * 1.1215, 0), 2) AS roas_real
```

> **Importante:** o `roas_compras` da view usa o investimento bruto (sem fator). Para ROAS real use a view `v_performance_mensal` no BigQuery ou calcule manualmente com `× 1.1215`.

---

## 5. Padrões de Query Mais Usados

### 5.1 — Resumo de período

```sql
SELECT
    COUNT(DISTINCT campanha)                                              AS campanhas_ativas,
    ROUND(SUM(investimento), 2)                                           AS investimento_total,
    ROUND(SUM(COALESCE(valor_compras, 0)), 2)                             AS receita_total,
    ROUND(SUM(COALESCE(compras, 0)), 0)                                   AS compras_total,
    ROUND(SUM(valor_compras) / NULLIF(SUM(investimento), 0), 2)           AS roas,
    ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(compras), 0), 2)       AS cpa_ajustado,
    ROUND(SUM(impressoes), 0)                                             AS impressoes,
    ROUND(CAST(SUM(cliques_link) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 2) AS ctr_pct
FROM campanhas_unificada
WHERE cliente_key = 'source_of'
  AND data_inicio >= '2026-09-01'
  AND data_fim    <= '2026-09-28'
LIMIT 1;
```

### 5.2 — Ranking de campanhas por ROAS

```sql
SELECT
    campanha,
    ROUND(SUM(investimento), 2)                                    AS investimento,
    ROUND(SUM(COALESCE(valor_compras, 0)), 2)                      AS receita,
    ROUND(SUM(COALESCE(compras, 0)), 0)                            AS compras,
    ROUND(SUM(valor_compras) / NULLIF(SUM(investimento), 0), 2)    AS roas,
    ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(compras), 0), 2) AS cpa_ajustado
FROM campanhas_unificada
WHERE cliente_key = 'source_of'
  AND data_inicio >= '2026-09-01'
  AND data_fim    <= '2026-09-28'
  AND LOWER(campanha) NOT LIKE '%tráfego%'
  AND LOWER(campanha) NOT LIKE '%awareness%'
GROUP BY campanha
HAVING SUM(investimento) >= 100
ORDER BY roas DESC
LIMIT 10;
```

### 5.3 — Comparativo temporal (semana atual vs semana anterior)

```sql
WITH semana_atual AS (
    SELECT
        campanha,
        SUM(investimento)               AS inv_a,
        SUM(COALESCE(valor_compras, 0)) AS rec_a,
        SUM(COALESCE(compras, 0))       AS comp_a
    FROM campanhas_unificada
    WHERE cliente_key = 'source_of'
      AND data_inicio >= '2026-09-22'
      AND data_fim    <= '2026-09-28'
    GROUP BY campanha
),
semana_anterior AS (
    SELECT
        campanha,
        SUM(investimento)               AS inv_b,
        SUM(COALESCE(valor_compras, 0)) AS rec_b,
        SUM(COALESCE(compras, 0))       AS comp_b
    FROM campanhas_unificada
    WHERE cliente_key = 'source_of'
      AND data_inicio >= '2026-09-15'
      AND data_fim    <= '2026-09-21'
    GROUP BY campanha
)
SELECT
    a.campanha,
    ROUND(a.rec_a / NULLIF(a.inv_a, 0), 2)  AS roas_atual,
    ROUND(b.rec_b / NULLIF(b.inv_b, 0), 2)  AS roas_anterior,
    ROUND((a.rec_a / NULLIF(a.inv_a, 0))
        - (b.rec_b / NULLIF(b.inv_b, 0)), 2) AS delta_roas,
    ROUND(a.inv_a, 2) AS invest_atual,
    ROUND(b.inv_b, 2) AS invest_anterior
FROM semana_atual a
LEFT JOIN semana_anterior b ON a.campanha = b.campanha
ORDER BY roas_atual DESC
LIMIT 20;
```

### 5.4 — Funil de conversão por campanha

```sql
SELECT
    campanha,
    ROUND(SUM(impressoes), 0)                                                        AS impressoes,
    ROUND(SUM(COALESCE(cliques_link, 0)), 0)                                         AS cliques,
    ROUND(SUM(COALESCE(visualizacoes_landing, 0)), 0)                                AS landing_views,
    ROUND(SUM(COALESCE(adicoes_carrinho, 0)), 0)                                     AS carrinho,
    ROUND(SUM(COALESCE(checkout_iniciado, 0)), 0)                                    AS checkout,
    ROUND(SUM(COALESCE(compras, 0)), 0)                                              AS compras,
    -- Taxas de conversão
    ROUND(CAST(SUM(cliques_link) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 2)    AS ctr_pct,
    ROUND(CAST(SUM(COALESCE(adicoes_carrinho,0)) AS FLOAT) / NULLIF(SUM(cliques_link),0)*100, 2) AS taxa_click_carrinho,
    ROUND(CAST(SUM(COALESCE(compras,0)) AS FLOAT) / NULLIF(SUM(COALESCE(checkout_iniciado,0)),0)*100, 2) AS taxa_checkout_compra
FROM campanhas_unificada
WHERE cliente_key = 'source_of'
  AND data_inicio >= '2026-09-01'
  AND data_fim    <= '2026-09-28'
GROUP BY campanha
HAVING SUM(investimento) >= 50
ORDER BY compras DESC
LIMIT 20;
```

### 5.5 — Distribuição de investimento (share de budget)

```sql
SELECT
    campanha,
    ROUND(SUM(investimento), 2) AS investimento,
    ROUND(
        SUM(investimento) * 100.0 /
        NULLIF(SUM(SUM(investimento)) OVER (), 0)
    , 1) AS share_pct,
    ROUND(SUM(valor_compras) / NULLIF(SUM(investimento), 0), 2) AS roas
FROM campanhas_unificada
WHERE cliente_key = 'source_of'
  AND data_inicio >= '2026-09-01'
  AND data_fim    <= '2026-09-28'
GROUP BY campanha
ORDER BY investimento DESC
LIMIT 20;
```

### 5.6 — Série histórica mensal

```sql
SELECT
    strftime('%Y-%m', data_inicio)                                  AS mes,
    ROUND(SUM(investimento), 2)                                     AS investimento,
    ROUND(SUM(COALESCE(valor_compras, 0)), 2)                       AS receita,
    ROUND(SUM(COALESCE(compras, 0)), 0)                             AS compras,
    ROUND(SUM(valor_compras) / NULLIF(SUM(investimento), 0), 2)     AS roas,
    COUNT(DISTINCT campanha)                                        AS campanhas_ativas
FROM campanhas_unificada
WHERE cliente_key = 'source_of'
GROUP BY strftime('%Y-%m', data_inicio)
ORDER BY mes DESC
LIMIT 12;
```

### 5.7 — Campanhas sem conversão (alertas)

```sql
SELECT
    campanha,
    ROUND(SUM(investimento), 2)  AS investimento,
    SUM(COALESCE(compras, 0))    AS compras,
    ROUND(SUM(impressoes), 0)    AS impressoes,
    MAX(data_fim)                AS ultimo_dado
FROM campanhas_unificada
WHERE cliente_key = 'source_of'
  AND data_inicio >= date('now', '-30 days')
GROUP BY campanha
HAVING SUM(compras) = 0
   AND SUM(investimento) >= 100
ORDER BY investimento DESC
LIMIT 20;
```

### 5.8 — Performance por plataforma (Meta vs Google)

```sql
SELECT
    plataforma,
    ROUND(SUM(investimento), 2)                                     AS investimento,
    ROUND(SUM(COALESCE(valor_compras, 0)), 2)                       AS receita,
    ROUND(SUM(COALESCE(compras, 0)), 0)                             AS compras,
    ROUND(SUM(valor_compras) / NULLIF(SUM(investimento), 0), 2)     AS roas,
    ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(compras), 0), 2) AS cpa_ajustado,
    COUNT(DISTINCT campanha)                                        AS campanhas
FROM campanhas_unificada
WHERE cliente_key = 'source_of'
  AND data_inicio >= '2026-09-01'
  AND data_fim    <= '2026-09-28'
GROUP BY plataforma
ORDER BY investimento DESC
LIMIT 5;
```

---

## 6. BigQuery — Queries Analíticas

### 6.1 — Schema BigQuery

**Tabela `ads_diario`** (espelho de Meta + Google):

| Coluna | Tipo | Descrição |
|---|---|---|
| `cliente` | STRING | `'source_of'` ou `'pod_kombucha'` |
| `plataforma` | STRING | `'meta'` ou `'google'` |
| `data` | DATE | Data da linha |
| `campanha` | STRING | Nome da campanha |
| `campaign_id` | STRING | ID da campanha |
| `adset_name` | STRING | Nome do conjunto (Meta only) |
| `ad_name` | STRING | Nome do anúncio (Meta only) |
| `investimento` | STRING (DECIMAL) | Gasto em R$ |
| `compras` | INT64 | Conversões |
| `receita` | STRING (DECIMAL) | Receita em R$ |
| `impressoes` | INT64 | Impressões |
| `cliques_link` | INT64 | Cliques no link |
| `roas` | FLOAT64 | ROAS calculado |
| `cpa` | FLOAT64 | CPA calculado |
| `criado_em` | TIMESTAMP | Timestamp de ingestão |

**Tabela `shopify_diario`** (receita Shopify):

| Coluna | Tipo | Descrição |
|---|---|---|
| `cliente` | STRING | Cliente |
| `data` | DATE | Data |
| `receita` | STRING (DECIMAL) | Receita total |
| `pedidos` | INT64 | Total de pedidos |
| `novos_clientes` | INT64 | Novos compradores |
| `recompradores` | INT64 | Compradores recorrentes |
| `ticket_medio` | STRING (DECIMAL) | Ticket médio |
| `total_desconto` | STRING (DECIMAL) | Total em descontos |
| `pedidos_acima_300` | INT64 | Pedidos acima de R$ 300 |

### 6.2 — View `v_performance_mensal`

A view já está criada no BigQuery. Use para análise consolidada:

```sql
SELECT
    cliente,
    FORMAT_DATE('%Y-%m', mes) AS mes,
    investimento_meta,
    investimento_google,
    investimento_total,
    receita_shopify,
    roas_real,           -- receita_shopify / investimento_total (com ajuste 1.1215 no meta)
    cpa,
    pct_retencao,
    pct_novos,
    var_receita_pct,     -- variação MoM em receita
    var_roas_pct         -- variação MoM em ROAS
FROM `guara_performance.v_performance_mensal`
WHERE cliente = 'source_of'
ORDER BY mes DESC
LIMIT 12;
```

### 6.3 — Query BQ: ranking de campanhas por período

```sql
SELECT
    campanha,
    SUM(CAST(investimento AS FLOAT64))  AS investimento,
    SUM(CAST(receita AS FLOAT64))       AS receita,
    SUM(compras)                        AS compras,
    ROUND(SUM(CAST(receita AS FLOAT64)) / NULLIF(SUM(CAST(investimento AS FLOAT64)), 0), 2) AS roas
FROM `guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY campanha
HAVING SUM(CAST(investimento AS FLOAT64)) >= 100
ORDER BY roas DESC
LIMIT 20;
```

> **Nota BQ:** as colunas `investimento`, `receita`, `ticket_medio`, `total_desconto` são armazenadas como STRING para evitar perda de precisão. Faça `CAST(... AS FLOAT64)` nas queries BQ.

---

## 7. Pipeline de SQL Dinâmico (Text-to-SQL)

O agente converte perguntas em linguagem natural para SQL automaticamente via `sql_generator.py`.

### Fluxo simples (perguntas diretas)

```
Pergunta → [Claude: gera SELECT] → validação → executa
```

### Fluxo com Query Planner (perguntas complexas)

Ativado automaticamente quando a pergunta contém: *compara, vs, semana passada, mês passado, período anterior, excluindo, tanto...quanto*...

```
Pergunta → [Claude: Planner → QueryPlan JSON] → [Claude: Generator → SELECT] → validação → executa
```

### Guardrails automáticos do validador (`sql_validator.py`)

| Camada | O que verifica | O que faz |
|---|---|---|
| **Segurança** | Apenas SELECT; sem INSERT/UPDATE/DELETE/DROP | Bloqueia se violado |
| **LIMIT** | LIMIT obrigatório, máximo 50 linhas | Injeta/reduz automaticamente |
| **cliente_key** | WHERE cliente_key obrigatório | Injeta automaticamente se ausente |
| **Schema** | Tabelas e colunas existem | Alerta (warning) se não encontrar |
| **Negócio** | AVG em ROAS/CPA, divisão sem NULLIF, datas não-ISO | Warning com orientação |
| **Performance** | SELECT *, query sem WHERE em tabela grande | Warning |

---

## 8. Conexão ao Banco (Python)

```python
from db.connections import query_fed, query_fed_one, LEGACY_DB_PATH

# Query que retorna lista de dicts
rows = query_fed("SELECT campanha, SUM(investimento) AS inv FROM campanhas_unificada WHERE cliente_key = ? GROUP BY campanha LIMIT 10", ("source_of",))

# Query que retorna um único dict
total = query_fed_one("SELECT SUM(investimento) AS total FROM campanhas_unificada WHERE cliente_key = ?", ("source_of",))
```

**Fallback:** se DuckDB falhar, `sql_generator.py` cai automaticamente para SQLite legado (`DB_PATH`).

---

## 9. Checklist de Query Segura

Antes de rodar qualquer SELECT, confirme:

- [ ] `WHERE cliente_key = '...'` presente
- [ ] Datas em formato `'YYYY-MM-DD'`
- [ ] ROAS calculado como `SUM(valor_compras) / NULLIF(SUM(investimento), 0)`
- [ ] CPA calculado como `SUM(investimento) / NULLIF(SUM(compras), 0)`
- [ ] `NULLIF` em todo denominador de divisão
- [ ] `COALESCE(coluna, 0)` em colunas que podem ser NULL (compras, valor_compras, cliques_link, etc.)
- [ ] `LIMIT` definido (máximo 50)
- [ ] GROUP BY incluindo todas as colunas não-agregadas no SELECT

---

## 10. Clientes e Valores de Referência

| `cliente_key` | `plataforma` | Conta |
|---|---|---|
| `source_of` | `meta_ads` | `act_1059884611898142` |
| `source_of` | `google_ads` | Customer ID `4311260590` |
| `pod_kombucha` | `meta_ads` | `act_324906945710366` |

**Fator de imposto Meta:** `1.1215` (aplicado sobre `investimento` para obter custo real)

**Thresholds padrão de alerta:**
- ROAS mínimo preocupante: `< 1.0`
- Investimento mínimo para entrar em ranking: `R$ 100`
- Investimento mínimo para análise de funil: `R$ 50`

---

*Cesar ⚙️ — Agente de Performance Guará*
