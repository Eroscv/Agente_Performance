# 📊 Guia BigQuery — Agente de Performance Guará

> **Console:** https://console.cloud.google.com/bigquery
> **Projeto:** `guara-db` | **Dataset:** `guara_performance`
> Cesar ⚙️ — atualizado em 28/09/2026

---

## 🧭 Como usar este guia

Este guia é a referência completa para consultar os dados de performance diretamente no BigQuery. Cada consulta tem quatro blocos:

- **O que faz** — objetivo da query
- **Quando usar** — situação prática
- **Como adaptar** — o que você precisa trocar para o seu caso
- **A query** — pronta para copiar e colar

**Para executar qualquer query:**
1. Acesse https://console.cloud.google.com/bigquery
2. No seletor superior, escolha o projeto **`guara-db`**
3. Clique em **+ Nova consulta** ou cole direto no editor
4. Pressione **Ctrl+Enter** ou clique em **Executar**

---

## 🗂️ Estrutura do banco

### Tabelas e views

| Objeto | Nome completo no BQ | O que contém |
|---|---|---|
| Tabela | `guara-db.guara_performance.ads_diario` | Dados diários de Meta Ads e Google Ads de todos os clientes |
| Tabela | `guara-db.guara_performance.shopify_diario` | Receita, pedidos e retenção Shopify |
| View | `guara-db.guara_performance.v_performance_mensal` | Consolidado mensal — ROAS real, CPA, retenção, MoM e YoY já calculados |

### Valores válidos dos campos de filtro

| Campo | Valores |
|---|---|
| `cliente` | `'source_of'` · `'pod_kombucha'` |
| `plataforma` | `'meta'` · `'google'` |

### Schema completo — `ads_diario`

| Coluna | Tipo BQ | Descrição |
|---|---|---|
| `cliente` | STRING | Identificador do cliente |
| `plataforma` | STRING | `'meta'` ou `'google'` |
| `data` | DATE | Data da linha |
| `campanha` | STRING | Nome da campanha |
| `campaign_id` | STRING | ID da campanha na plataforma |
| `adset_name` | STRING | Nome do conjunto de anúncios *(Meta only)* |
| `adset_id` | STRING | ID do conjunto *(Meta only)* |
| `ad_name` | STRING | Nome do criativo/anúncio *(Meta only)* |
| `ad_id` | STRING | ID do anúncio *(Meta only)* |
| `objetivo` | STRING | Objetivo da campanha |
| `status` | STRING | Status da campanha |
| `investimento` | **STRING ⚠️** | Gasto em R$ — **sempre usar CAST AS FLOAT64** |
| `compras` | INT64 | Número de conversões |
| `receita` | **STRING ⚠️** | Receita em R$ — **sempre usar CAST AS FLOAT64** |
| `impressoes` | INT64 | Total de impressões |
| `alcance` | INT64 | Pessoas únicas alcançadas |
| `cliques_link` | INT64 | Cliques no link do anúncio |
| `cliques_todos` | INT64 | Todos os cliques (inclusive engajamento) |
| `roas` | FLOAT64 | ROAS bruto reportado pela plataforma |
| `cpa` | FLOAT64 | CPA bruto reportado |
| `cpm` | FLOAT64 | Custo por mil impressões |
| `frequencia` | FLOAT64 | impressoes / alcance |
| `video_3s` | INT64 | Views de vídeo ≥ 3s *(Meta only)* |
| `video_50pct` | INT64 | Views até 50% do vídeo *(Meta only)* |
| `thruplays` | INT64 | Views completas *(Meta only)* |
| `adicoes_carrinho` | INT64 | Adições ao carrinho *(Meta only)* |
| `checkout_iniciado` | INT64 | Checkouts iniciados *(Meta only)* |
| `visualizacoes_landing` | INT64 | Views da landing page *(Meta only)* |
| `qualidade_ranking` | STRING | Ranking de qualidade *(Meta only)* |
| `engagement_ranking` | STRING | Ranking de engajamento *(Meta only)* |
| `conversion_ranking` | STRING | Ranking de conversão *(Meta only)* |
| `criado_em` | TIMESTAMP | Timestamp de ingestão no BQ |

### Schema completo — `shopify_diario`

| Coluna | Tipo BQ | Descrição |
|---|---|---|
| `cliente` | STRING | Identificador do cliente |
| `data` | DATE | Data |
| `receita` | **STRING ⚠️** | Receita total — **sempre CAST AS FLOAT64** |
| `pedidos` | INT64 | Total de pedidos |
| `novos_clientes` | INT64 | Compradores pela primeira vez |
| `recompradores` | INT64 | Clientes que já compraram antes |
| `ticket_medio` | **STRING ⚠️** | Ticket médio — **sempre CAST AS FLOAT64** |
| `total_desconto` | **STRING ⚠️** | Total em descontos — **sempre CAST AS FLOAT64** |
| `pedidos_acima_300` | INT64 | Pedidos com valor ≥ R$ 300 |
| `criado_em` | TIMESTAMP | Timestamp de ingestão |

---

## ⚠️ Regras obrigatórias

### Regra 1 — Sempre filtrar por `cliente`

Nunca execute uma query sem `WHERE cliente = '...'`. Sem esse filtro os dados dos dois clientes se misturam e os resultados ficam incorretos.

```sql
-- ✅ Correto
WHERE cliente = 'source_of'

-- ❌ Errado: mistura Source OF e POD Kombucha
```

### Regra 2 — Colunas monetárias são STRING → sempre CAST

As colunas `investimento`, `receita`, `ticket_medio` e `total_desconto` são STRING no BigQuery. Sem o CAST, operações matemáticas retornam erro.

```sql
-- ✅ Correto
SUM(CAST(investimento AS FLOAT64))
SUM(CAST(receita AS FLOAT64))

-- ❌ Errado: retorna erro
SUM(investimento)
```

### Regra 3 — ROAS sempre ponderado, nunca AVG

`AVG(roas)` calcula a média simples dos ROAS diários — matematicamente incorreto. ROAS correto = receita total ÷ investimento total:

```sql
-- ✅ Correto: ROAS ponderado
ROUND(
  SUM(CAST(receita AS FLOAT64)) /
  NULLIF(SUM(CAST(investimento AS FLOAT64)), 0)
, 2) AS roas_bruto

-- ❌ Errado: distorce o resultado
AVG(roas)
```

### Regra 4 — Fator de imposto Meta (× 1.1215)

O Meta Ads não inclui impostos no gasto reportado. Para ROAS real e CPA real, multiplique o investimento por **1.1215**:

```sql
-- ROAS real (Meta)
ROUND(
  SUM(CAST(receita AS FLOAT64)) /
  NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
, 2) AS roas_real

-- CPA real (Meta)
ROUND(
  SUM(CAST(investimento AS FLOAT64)) * 1.1215 /
  NULLIF(SUM(compras), 0)
, 2) AS cpa_real
```

> **Atenção:** Google Ads **não tem** esse ajuste. Só aplique `× 1.1215` quando `plataforma = 'meta'` ou em análises que incluam Meta.

### Regra 5 — Use SAFE_DIVIDE para divisões que podem ser zero

```sql
-- ✅ SAFE_DIVIDE retorna NULL ao invés de erro quando denominador = 0
ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2) AS ctr_pct

-- Alternativa equivalente com NULLIF
ROUND(SUM(cliques_link) / NULLIF(SUM(impressoes), 0) * 100, 2) AS ctr_pct
```

### Regra 6 — Datas em formato ISO YYYY-MM-DD

```sql
-- ✅ Correto
WHERE data BETWEEN '2026-09-01' AND '2026-09-28'

-- ❌ Errado
WHERE data BETWEEN '01/09/2026' AND '28/09/2026'
```

---

## 📋 Índice de consultas

| # | Consulta | Tipo |
|---|---|---|
| 1 | Diagnóstico do banco | Exploração |
| 2 | Resumo consolidado de período | Visão geral |
| 3 | Ranking de campanhas por ROAS real | Performance |
| 4 | Ranking por compras e receita | Performance |
| 5 | Comparativo semana a semana | Temporal |
| 6 | Comparativo mês a mês (MoM) | Temporal |
| 7 | Comparativo ano a ano (YoY) | Temporal |
| 8 | View mensal pronta — v_performance_mensal | Analítica |
| 9 | Série diária — evolução dia a dia | Temporal |
| 10 | Volatilidade de ROAS dia a dia | Diagnóstico |
| 11 | Funil de conversão por campanha | Funil |
| 12 | Distribuição de budget (share %) | Budget |
| 13 | Shopify — receita e retenção mensal | E-commerce |
| 14 | ROAS real: Shopify × Meta (join manual) | E-commerce |
| 15 | Criativos — top ROAS por anúncio | Criativo |
| 16 | Criativos em fadiga | Criativo |
| 17 | Métricas de vídeo | Vídeo |
| 18 | Alerta: campanhas com ROAS baixo | Alerta |
| 19 | Alerta: campanhas sem conversão | Alerta |
| 20 | Projeção de fechamento do mês | Projeção |
| 21 | Dois clientes lado a lado | Comparativo |
| 22 | Meta vs Google — por plataforma | Comparativo |
| 23 | Ciclo de vida de campanhas | Histórico |
| 24 | Ticket médio e pedidos premium | E-commerce |
| 25 | Top campanhas por CTR e CPM | Engajamento |

---
## 1. Diagnóstico — o que tem no banco

**O que faz:** Mostra a cobertura de datas, total de linhas e campanhas disponíveis por cliente e plataforma. Primeira query a rodar quando quiser entender o estado do banco.

**Quando usar:** Antes de qualquer análise — para confirmar que os dados estão chegando e identificar o período disponível.

**Como adaptar:** Nenhuma adaptação necessária. Rode como está para ver o panorama completo.

```sql
-- Cobertura por cliente e plataforma (ads)
SELECT
  cliente,
  plataforma,
  MIN(data)                AS primeiro_dia,
  MAX(data)                AS ultimo_dia,
  COUNT(*)                 AS total_linhas,
  COUNT(DISTINCT campanha) AS total_campanhas,
  COUNT(DISTINCT data)     AS dias_com_dados
FROM `guara-db.guara_performance.ads_diario`
GROUP BY cliente, plataforma
ORDER BY cliente, plataforma;
```

```sql
-- Cobertura Shopify
SELECT
  cliente,
  MIN(data)                           AS primeiro_dia,
  MAX(data)                           AS ultimo_dia,
  COUNT(*)                            AS total_dias,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2) AS receita_acumulada
FROM `guara-db.guara_performance.shopify_diario`
GROUP BY cliente;
```

---

## 2. Resumo consolidado de período

**O que faz:** Retorna os totais de investimento, receita, compras, ROAS real, CPA real, impressões e CTR para um período e cliente específicos.

**Quando usar:** Para responder perguntas do tipo "como foi setembro?" ou "qual foi o resultado da semana passada?".

**Como adaptar:**
- Troque `'source_of'` pelo cliente desejado
- Troque `'meta'` por `'google'` para ver Google Ads, ou remova o filtro de plataforma para ver tudo junto
- Ajuste as datas no `BETWEEN`

```sql
SELECT
  COUNT(DISTINCT campanha)                                                  AS campanhas_ativas,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento_total,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita_total,
  SUM(compras)                                                              AS compras_total,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)), 0)
  , 2)                                                                      AS roas_bruto,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                      AS roas_real,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 1.1215 /
    NULLIF(SUM(compras), 0)
  , 2)                                                                      AS cpa_real,
  ROUND(SUM(impressoes), 0)                                                 AS impressoes,
  ROUND(SUM(alcance), 0)                                                    AS alcance,
  ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2)           AS ctr_pct,
  ROUND(SAFE_DIVIDE(SUM(CAST(investimento AS FLOAT64)), SUM(impressoes)) * 1000, 2) AS cpm_medio
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente    = 'source_of'    -- ← troque o cliente se necessário
  AND plataforma = 'meta'          -- ← remova esta linha para ver todas as plataformas
  AND data BETWEEN '2026-09-01' AND '2026-09-28';  -- ← ajuste o período
```

---

## 3. Ranking de campanhas por ROAS real

**O que faz:** Lista as campanhas ordenadas pelo ROAS real (com fator de imposto Meta), excluindo campanhas de awareness que não têm objetivo de conversão e campanhas com pouco investimento.

**Quando usar:** Para identificar quais campanhas estão trazendo o melhor retorno e merecem mais budget.

**Como adaptar:**
- Troque as datas no `BETWEEN`
- Ajuste `HAVING SUM(...) >= 100` para um mínimo de investimento diferente
- Adicione ou remova filtros de campanha no `NOT LIKE`
- Troque `LIMIT 15` pelo número de resultados desejado

```sql
SELECT
  campanha,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita,
  SUM(compras)                                                              AS compras,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                      AS roas_real,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 1.1215 /
    NULLIF(SUM(compras), 0)
  , 2)                                                                      AS cpa_real,
  ROUND(SUM(impressoes), 0)                                                 AS impressoes,
  ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2)           AS ctr_pct
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente    = 'source_of'
  AND plataforma = 'meta'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
  AND LOWER(campanha) NOT LIKE '%tráfego%'     -- exclui awareness/tráfego
  AND LOWER(campanha) NOT LIKE '%awareness%'
  AND LOWER(campanha) NOT LIKE '%engajamento%'
GROUP BY campanha
HAVING SUM(CAST(investimento AS FLOAT64)) >= 100  -- mínimo R$100 investidos
ORDER BY roas_real DESC
LIMIT 15;
```

---

## 4. Ranking por compras e receita

**O que faz:** Top campanhas ordenadas por volume de compras e receita gerada, com ROAS e CPA para contexto.

**Quando usar:** Quando o objetivo é volume — saber quais campanhas geram mais vendas absolutas, independente do ROAS.

**Como adaptar:** Troque `ORDER BY compras DESC` por `ORDER BY receita DESC` para priorizar receita.

```sql
SELECT
  campanha,
  plataforma,
  SUM(compras)                                                              AS compras,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                      AS roas_real,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 1.1215 /
    NULLIF(SUM(compras), 0)
  , 2)                                                                      AS cpa_real,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) * 100.0 /
    NULLIF(SUM(SUM(CAST(receita AS FLOAT64))) OVER (), 0)
  , 1)                                                                      AS share_receita_pct
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY campanha, plataforma
HAVING SUM(compras) > 0
ORDER BY compras DESC
LIMIT 20;
```

---

## 5. Comparativo semana a semana

**O que faz:** Compara o desempenho das campanhas entre duas semanas — ROAS, investimento e variação percentual. Usa CTEs (WITH) para calcular os dois períodos separadamente e depois fazer o JOIN.

**Quando usar:** No acompanhamento semanal de performance ou para entender se uma alteração em campanhas melhorou ou piorou os resultados.

**Como adaptar:**
- Ajuste as datas nos dois blocos de CTE (`semana_atual` e `semana_anterior`)
- As semanas não precisam ter exatamente 7 dias — use qualquer dois períodos que queira comparar

```sql
WITH semana_atual AS (
  SELECT
    campanha,
    SUM(CAST(investimento AS FLOAT64)) AS inv_a,
    SUM(CAST(receita AS FLOAT64))      AS rec_a,
    SUM(compras)                       AS comp_a,
    SUM(impressoes)                    AS imp_a
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente    = 'source_of'
    AND plataforma = 'meta'
    AND data BETWEEN '2026-09-22' AND '2026-09-28'  -- ← semana atual
  GROUP BY campanha
),
semana_anterior AS (
  SELECT
    campanha,
    SUM(CAST(investimento AS FLOAT64)) AS inv_b,
    SUM(CAST(receita AS FLOAT64))      AS rec_b,
    SUM(compras)                       AS comp_b,
    SUM(impressoes)                    AS imp_b
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente    = 'source_of'
    AND plataforma = 'meta'
    AND data BETWEEN '2026-09-15' AND '2026-09-21'  -- ← semana anterior
  GROUP BY campanha
)
SELECT
  a.campanha,
  ROUND(a.rec_a / NULLIF(a.inv_a * 1.1215, 0), 2)              AS roas_atual,
  ROUND(b.rec_b / NULLIF(b.inv_b * 1.1215, 0), 2)              AS roas_anterior,
  ROUND(
    (a.rec_a / NULLIF(a.inv_a * 1.1215, 0)) -
    (b.rec_b / NULLIF(b.inv_b * 1.1215, 0))
  , 2)                                                          AS delta_roas,
  ROUND(SAFE_DIVIDE(a.rec_a - b.rec_b, b.rec_b) * 100, 1)      AS var_receita_pct,
  ROUND(a.inv_a, 2)                                             AS invest_atual,
  ROUND(b.inv_b, 2)                                             AS invest_anterior,
  ROUND(SAFE_DIVIDE(a.inv_a - b.inv_b, b.inv_b) * 100, 1)      AS var_invest_pct,
  a.comp_a                                                      AS compras_atual,
  b.comp_b                                                      AS compras_anterior
FROM semana_atual a
LEFT JOIN semana_anterior b USING (campanha)
ORDER BY roas_atual DESC NULLS LAST
LIMIT 20;
```

---

## 6. Comparativo mês a mês (MoM)

**O que faz:** Compara o mês atual com o mês anterior de forma dinâmica — sem precisar trocar datas. Usa `CURRENT_DATE()` para sempre puxar os períodos corretos.

**Quando usar:** No relatório mensal ou sempre que quiser ver a variação em relação ao mês passado.

**Como adaptar:** Troque `'source_of'` pelo cliente desejado. Para comparar meses específicos no passado, substitua `DATE_TRUNC(CURRENT_DATE(), MONTH)` por `DATE '2026-08-01'` e `DATE '2026-09-01'`.

```sql
WITH mes_atual AS (
  SELECT
    SUM(CAST(investimento AS FLOAT64)) AS inv,
    SUM(CAST(receita AS FLOAT64))      AS rec,
    SUM(compras)                       AS comp,
    SUM(impressoes)                    AS imp,
    COUNT(DISTINCT campanha)           AS campanhas
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente = 'source_of'
    AND DATE_TRUNC(data, MONTH) = DATE_TRUNC(CURRENT_DATE(), MONTH)
),
mes_anterior AS (
  SELECT
    SUM(CAST(investimento AS FLOAT64)) AS inv,
    SUM(CAST(receita AS FLOAT64))      AS rec,
    SUM(compras)                       AS comp,
    SUM(impressoes)                    AS imp,
    COUNT(DISTINCT campanha)           AS campanhas
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente = 'source_of'
    AND DATE_TRUNC(data, MONTH) = DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH)
)
SELECT
  ROUND(a.inv, 2)                                               AS invest_atual,
  ROUND(b.inv, 2)                                               AS invest_anterior,
  ROUND(SAFE_DIVIDE(a.inv - b.inv, b.inv) * 100, 1)            AS var_invest_pct,
  ROUND(a.rec / NULLIF(a.inv * 1.1215, 0), 2)                  AS roas_atual,
  ROUND(b.rec / NULLIF(b.inv * 1.1215, 0), 2)                  AS roas_anterior,
  ROUND(
    (a.rec / NULLIF(a.inv * 1.1215, 0)) -
    (b.rec / NULLIF(b.inv * 1.1215, 0))
  , 2)                                                          AS delta_roas,
  a.comp                                                        AS compras_atual,
  b.comp                                                        AS compras_anterior,
  ROUND(SAFE_DIVIDE(a.comp - b.comp, b.comp) * 100, 1)         AS var_compras_pct,
  ROUND(a.rec, 2)                                               AS receita_atual,
  ROUND(b.rec, 2)                                               AS receita_anterior,
  ROUND(SAFE_DIVIDE(a.rec - b.rec, b.rec) * 100, 1)            AS var_receita_pct
FROM mes_atual a, mes_anterior b;
```

---

## 7. Comparativo ano a ano (YoY)

**O que faz:** Compara um período específico com o mesmo período do ano anterior. Ideal para detectar sazonalidade (Dia das Mães, Black Friday, etc).

**Quando usar:** Para justificar quedas ou picos que são sazonais ("em setembro do ano passado também foi assim").

**Como adaptar:** Ajuste `periodo_atual` com as datas que quer analisar. O `periodo_anterior` é calculado automaticamente subtraindo 1 ano.

```sql
WITH periodo_atual AS (
  SELECT
    SUM(CAST(investimento AS FLOAT64)) AS inv,
    SUM(CAST(receita AS FLOAT64))      AS rec,
    SUM(compras)                       AS comp
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente = 'source_of'
    AND data BETWEEN '2026-09-01' AND '2026-09-28'  -- ← período atual
),
periodo_anterior AS (
  SELECT
    SUM(CAST(investimento AS FLOAT64)) AS inv,
    SUM(CAST(receita AS FLOAT64))      AS rec,
    SUM(compras)                       AS comp
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente = 'source_of'
    AND data BETWEEN '2025-09-01' AND '2025-09-28'  -- ← mesmo período, ano anterior
)
SELECT
  ROUND(a.rec / NULLIF(a.inv * 1.1215, 0), 2)               AS roas_2026,
  ROUND(b.rec / NULLIF(b.inv * 1.1215, 0), 2)               AS roas_2025,
  ROUND(SAFE_DIVIDE(a.rec - b.rec, b.rec) * 100, 1)         AS var_receita_yoy_pct,
  ROUND(SAFE_DIVIDE(
    (a.rec / NULLIF(a.inv * 1.1215, 0)) -
    (b.rec / NULLIF(b.inv * 1.1215, 0)),
    b.rec / NULLIF(b.inv * 1.1215, 0)
  ) * 100, 1)                                               AS var_roas_yoy_pct,
  ROUND(a.inv, 2)                                           AS invest_2026,
  ROUND(b.inv, 2)                                           AS invest_2025,
  a.comp                                                    AS compras_2026,
  b.comp                                                    AS compras_2025
FROM periodo_atual a, periodo_anterior b;
```

---

## 8. View mensal pronta — `v_performance_mensal`

**O que faz:** A view já cruza `ads_diario` com `shopify_diario` e calcula ROAS real, CPA, retenção, variação MoM e YoY. Toda a lógica de cálculo já está embutida. **É a query mais completa para análise mensal.**

**Quando usar:** Para qualquer análise de performance mensal. Prefira esta view a fazer o cruzamento manual.

**Como adaptar:** Filtre por `cliente`, ajuste `LIMIT` e os campos que quer ver.

```sql
-- Histórico mensal completo — Source OF
SELECT
  FORMAT_DATE('%Y-%m', mes)   AS mes,
  investimento_meta,
  investimento_google,
  investimento_total,
  receita_shopify,
  roas_real,             -- receita_shopify / invest_total (meta já com ×1.1215)
  cpa,
  compras_total,
  pct_retencao,          -- % pedidos de recompradores
  pct_novos,             -- % pedidos de novos clientes
  pct_premium,           -- % pedidos acima de R$300
  ticket_medio,
  var_receita_pct,       -- variação MoM em receita
  var_roas_pct,          -- variação MoM em ROAS
  var_invest_pct,        -- variação MoM em investimento
  var_receita_yoy_pct,   -- variação YoY em receita
  var_roas_yoy_pct       -- variação YoY em ROAS
FROM `guara-db.guara_performance.v_performance_mensal`
WHERE cliente = 'source_of'
ORDER BY mes DESC
LIMIT 12;
```

```sql
-- Alerta: meses com ROAS real abaixo de 2.0
SELECT
  FORMAT_DATE('%Y-%m', mes) AS mes,
  cliente,
  ROUND(roas_real, 2)       AS roas_real,
  ROUND(investimento_total, 2) AS investimento,
  ROUND(receita_shopify, 2) AS receita,
  var_roas_pct
FROM `guara-db.guara_performance.v_performance_mensal`
WHERE roas_real < 2.0
  AND investimento_total > 1000
ORDER BY mes DESC, roas_real ASC;
```

---
## 9. Série diária — evolução dia a dia

**O que faz:** Retorna os totais diários de investimento, receita, compras e ROAS para acompanhar a evolução ao longo de um período.

**Quando usar:** Para identificar dias de pico, quedas bruscas ou padrões de sazonalidade dentro do período.

**Como adaptar:** Adicione `AND campanha = 'nome'` para ver a série de uma campanha específica. Remova o filtro de plataforma para ver o total consolidado.

```sql
SELECT
  data,
  plataforma,
  COUNT(DISTINCT campanha)                                                  AS campanhas_ativas,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita,
  SUM(compras)                                                              AS compras,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                      AS roas_real,
  ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2)           AS ctr_pct,
  ROUND(SUM(impressoes), 0)                                                 AS impressoes
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY data, plataforma
ORDER BY data DESC, plataforma;
```

---

## 10. Volatilidade de ROAS dia a dia

**O que faz:** Mostra o ROAS de cada dia e a variação percentual em relação ao dia anterior, usando a função de janela `LAG`. Permite identificar instabilidade e dias problemáticos.

**Quando usar:** Quando o ROAS médio parece ok mas você suspeita de oscilações — para visualizar picos e vales no período.

**Como adaptar:** Adicione filtro de campanha se quiser ver a volatilidade de uma campanha específica.

```sql
WITH diario AS (
  SELECT
    data,
    SUM(CAST(investimento AS FLOAT64)) AS inv,
    SUM(CAST(receita AS FLOAT64))      AS rec,
    SUM(compras)                       AS comp
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente    = 'source_of'
    AND plataforma = 'meta'
    AND data BETWEEN '2026-09-01' AND '2026-09-28'
  GROUP BY data
)
SELECT
  data,
  ROUND(inv, 2)                                                             AS investimento,
  ROUND(rec, 2)                                                             AS receita,
  comp                                                                      AS compras,
  ROUND(rec / NULLIF(inv * 1.1215, 0), 2)                                  AS roas_dia,
  ROUND(
    LAG(rec / NULLIF(inv * 1.1215, 0)) OVER (ORDER BY data)
  , 2)                                                                      AS roas_dia_anterior,
  ROUND(
    SAFE_DIVIDE(
      (rec / NULLIF(inv * 1.1215, 0)) -
      LAG(rec / NULLIF(inv * 1.1215, 0)) OVER (ORDER BY data),
      LAG(rec / NULLIF(inv * 1.1215, 0)) OVER (ORDER BY data)
    ) * 100
  , 1)                                                                      AS var_roas_pct
FROM diario
ORDER BY data DESC;
```

---

## 11. Funil de conversão por campanha

**O que faz:** Mostra cada etapa do funil (impressões → cliques → landing → carrinho → checkout → compra) com as taxas de conversão entre etapas. Identifica onde o funil está "vazando".

**Quando usar:** Quando o ROAS está baixo e você quer entender se o problema é alcance, cliques, ou conversão final.

**Como adaptar:** Remova colunas de Meta-only (`adicoes_carrinho`, `checkout_iniciado`, `visualizacoes_landing`) se quiser usar com Google Ads.

```sql
SELECT
  campanha,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                                          AS investimento,
  ROUND(SUM(impressoes), 0)                                                             AS impressoes,
  ROUND(SUM(cliques_link), 0)                                                           AS cliques,
  ROUND(SUM(visualizacoes_landing), 0)                                                  AS landing_views,
  ROUND(SUM(adicoes_carrinho), 0)                                                       AS carrinho,
  ROUND(SUM(checkout_iniciado), 0)                                                      AS checkout,
  SUM(compras)                                                                          AS compras,
  -- Taxas de conversão em cada etapa
  ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2)                      AS ctr_pct,
  ROUND(SAFE_DIVIDE(SUM(visualizacoes_landing), SUM(cliques_link)) * 100, 2)           AS taxa_landing_pct,
  ROUND(SAFE_DIVIDE(SUM(adicoes_carrinho), SUM(cliques_link)) * 100, 2)                AS taxa_carrinho_pct,
  ROUND(SAFE_DIVIDE(SUM(checkout_iniciado), SUM(adicoes_carrinho)) * 100, 2)           AS taxa_checkout_pct,
  ROUND(SAFE_DIVIDE(SUM(compras), SUM(checkout_iniciado)) * 100, 2)                    AS taxa_compra_pct,
  ROUND(SAFE_DIVIDE(SUM(compras), SUM(cliques_link)) * 100, 2)                         AS taxa_conversao_geral_pct,
  -- Custo por etapa
  ROUND(SAFE_DIVIDE(SUM(CAST(investimento AS FLOAT64)), SUM(adicoes_carrinho)), 2)      AS custo_por_carrinho,
  ROUND(SAFE_DIVIDE(SUM(CAST(investimento AS FLOAT64)), SUM(checkout_iniciado)), 2)     AS custo_por_checkout,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 1.1215 / NULLIF(SUM(compras), 0)
  , 2)                                                                                  AS cpa_real
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente    = 'source_of'
  AND plataforma = 'meta'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY campanha
HAVING SUM(CAST(investimento AS FLOAT64)) >= 50
ORDER BY compras DESC
LIMIT 20;
```

---

## 12. Distribuição de budget (share %)

**O que faz:** Mostra quanto % do investimento total foi para cada campanha, junto com o ROAS de cada uma. Revela se o dinheiro está indo para as campanhas certas.

**Quando usar:** Para auditoria de budget — quando quer saber se a distribuição entre campanhas está alinhada com o desempenho.

```sql
SELECT
  campanha,
  plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 100.0 /
    NULLIF(SUM(SUM(CAST(investimento AS FLOAT64))) OVER (), 0)
  , 1)                                                                      AS share_invest_pct,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                      AS roas_real,
  SUM(compras)                                                              AS compras,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) * 100.0 /
    NULLIF(SUM(SUM(CAST(receita AS FLOAT64))) OVER (), 0)
  , 1)                                                                      AS share_receita_pct
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY campanha, plataforma
ORDER BY investimento DESC
LIMIT 20;
```

---

## 13. Shopify — receita e retenção mensal

**O que faz:** Agrega os dados Shopify por mês: receita, pedidos, novos compradores, recompradores e taxa de retenção.

**Quando usar:** Para acompanhar a saúde do e-commerce além dos ads — entender se a base de clientes está crescendo ou se é só recompra.

**Como adaptar:** Troque `FORMAT_DATE('%Y-%m', data)` por `data` para ver dia a dia.

```sql
SELECT
  FORMAT_DATE('%Y-%m', data)                                                AS mes,
  SUM(pedidos)                                                              AS pedidos,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita,
  SUM(novos_clientes)                                                       AS novos_clientes,
  SUM(recompradores)                                                        AS recompradores,
  ROUND(SAFE_DIVIDE(SUM(recompradores), SUM(pedidos)) * 100, 1)             AS pct_retencao,
  ROUND(SAFE_DIVIDE(SUM(novos_clientes), SUM(pedidos)) * 100, 1)            AS pct_novos,
  ROUND(SAFE_DIVIDE(SUM(CAST(receita AS FLOAT64)), SUM(pedidos)), 2)        AS ticket_medio_real,
  SUM(pedidos_acima_300)                                                    AS pedidos_premium,
  ROUND(SAFE_DIVIDE(SUM(pedidos_acima_300), SUM(pedidos)) * 100, 1)         AS pct_premium
FROM `guara-db.guara_performance.shopify_diario`
WHERE cliente = 'source_of'
GROUP BY mes
ORDER BY mes DESC
LIMIT 12;
```

---

## 14. ROAS real: Shopify × Meta (join manual)

**O que faz:** Cruza os dados de investimento Meta com a receita real da Shopify mês a mês — calculando o ROAS com a receita que realmente entrou na loja, não a reportada pelo Meta.

**Quando usar:** Para comparar o ROAS reportado pelo Meta com o ROAS real baseado em receita Shopify — a diferença costuma ser significativa.

```sql
WITH ads AS (
  SELECT
    DATE_TRUNC(data, MONTH)            AS mes,
    ROUND(SUM(CAST(investimento AS FLOAT64)), 2) AS invest_meta,
    ROUND(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 2) AS invest_com_imposto,
    ROUND(SUM(CAST(receita AS FLOAT64)), 2) AS receita_meta_reportada,
    SUM(compras)                       AS compras_meta
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente    = 'source_of'
    AND plataforma = 'meta'
  GROUP BY mes
),
shopify AS (
  SELECT
    DATE_TRUNC(data, MONTH)            AS mes,
    ROUND(SUM(CAST(receita AS FLOAT64)), 2) AS receita_shopify,
    SUM(pedidos)                       AS pedidos_shopify
  FROM `guara-db.guara_performance.shopify_diario`
  WHERE cliente = 'source_of'
  GROUP BY mes
)
SELECT
  FORMAT_DATE('%Y-%m', a.mes)                                               AS mes,
  a.invest_meta,
  a.invest_com_imposto,
  a.receita_meta_reportada,
  s.receita_shopify,
  ROUND(a.receita_meta_reportada / NULLIF(a.invest_com_imposto, 0), 2)      AS roas_meta_reportado,
  ROUND(s.receita_shopify / NULLIF(a.invest_com_imposto, 0), 2)             AS roas_real_shopify,
  ROUND(s.receita_shopify - a.receita_meta_reportada, 2)                   AS diferenca_receita,
  s.pedidos_shopify,
  a.compras_meta
FROM ads a
JOIN shopify s USING (mes)
ORDER BY mes DESC
LIMIT 12;
```

---
## 15. Criativos — top ROAS por anúncio (Meta only)

**O que faz:** Analisa a performance no nível do anúncio individual (`ad_name`), mostrando quais criativos específicos têm melhor ROAS, CTR e CPM. Só disponível para Meta Ads — Google Ads não tem esse nível de detalhe.

**Quando usar:** Para decidir quais criativos escalar, pausar ou duplicar.

**Como adaptar:** Adicione `AND campanha LIKE '%nome%'` para filtrar por uma campanha específica.

```sql
SELECT
  ad_name                                                                   AS criativo,
  adset_name                                                                AS conjunto,
  campanha,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento,
  SUM(compras)                                                              AS compras,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                      AS roas_real,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 1.1215 /
    NULLIF(SUM(compras), 0)
  , 2)                                                                      AS cpa_real,
  ROUND(SUM(impressoes), 0)                                                 AS impressoes,
  ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2)           AS ctr_pct,
  ROUND(SAFE_DIVIDE(SUM(CAST(investimento AS FLOAT64)), SUM(impressoes)) * 1000, 2) AS cpm,
  ROUND(SAFE_DIVIDE(SUM(impressoes), SUM(alcance)), 2)                      AS frequencia,
  MAX(data)                                                                 AS ultima_atividade
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente    = 'source_of'
  AND plataforma = 'meta'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
  AND ad_name IS NOT NULL
GROUP BY ad_name, adset_name, campanha
HAVING SUM(CAST(investimento AS FLOAT64)) >= 50
ORDER BY roas_real DESC NULLS LAST
LIMIT 20;
```

---

## 16. Criativos em fadiga

**O que faz:** Identifica criativos com frequência alta (audiência já saturada) **e** CTR abaixo da média da conta — os principais candidatos a pausar ou renovar. Usa CTE para calcular a média de CTR e comparar cada criativo contra ela.

**Quando usar:** Quando a performance está caindo sem motivo aparente — frequência alta é a causa mais comum de fadiga criativa.

**Como adaptar:** Ajuste `frequencia >= 3.5` para o threshold desejado (acima de 3.0 já é sinal de atenção).

```sql
WITH base AS (
  SELECT
    ad_name                                                                AS criativo,
    adset_name,
    campanha,
    ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                           AS investimento,
    ROUND(SAFE_DIVIDE(SUM(impressoes), SUM(alcance)), 2)                   AS frequencia,
    ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2)        AS ctr,
    SUM(compras)                                                           AS compras,
    ROUND(SUM(CAST(receita AS FLOAT64)) / NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0), 2) AS roas_real,
    MAX(data)                                                              AS ultima_atividade
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente    = 'source_of'
    AND plataforma = 'meta'
    AND data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
    AND ad_name IS NOT NULL
    AND alcance > 0
  GROUP BY ad_name, adset_name, campanha
  HAVING SUM(CAST(investimento AS FLOAT64)) >= 20
),
media_conta AS (
  SELECT AVG(ctr) AS ctr_medio_conta
  FROM base
  WHERE ctr > 0
)
SELECT
  b.criativo,
  b.conjunto,
  b.campanha,
  b.investimento,
  b.frequencia,
  b.ctr,
  ROUND(m.ctr_medio_conta, 2)                                              AS ctr_medio_conta,
  ROUND(b.ctr - m.ctr_medio_conta, 2)                                      AS delta_ctr,
  b.compras,
  b.roas_real,
  b.ultima_atividade
FROM base b, media_conta m
WHERE b.frequencia >= 3.5                -- ← ajuste o threshold de frequência
  AND b.ctr < m.ctr_medio_conta          -- CTR abaixo da média da conta
ORDER BY b.frequencia DESC, b.investimento DESC
LIMIT 20;
```

---

## 17. Métricas de vídeo

**O que faz:** Analisa a performance de vídeo: reproduções, views de 3s, thruplays (views completas), taxa de retenção e custo por thruplay. Disponível apenas para Meta Ads.

**Quando usar:** Para avaliar campanhas com criativos em vídeo — saber se o vídeo está sendo assistido até o fim ou se as pessoas pulam logo no início.

```sql
SELECT
  campanha,
  ad_name                                                                   AS criativo,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento,
  ROUND(SUM(impressoes), 0)                                                 AS impressoes,
  ROUND(SUM(video_3s), 0)                                                   AS views_3s,
  ROUND(SUM(video_50pct), 0)                                                AS views_50pct,
  ROUND(SUM(thruplays), 0)                                                  AS thruplays,
  -- Taxa de retenção: ThruPlays / views 3s
  ROUND(SAFE_DIVIDE(SUM(thruplays), SUM(video_3s)) * 100, 1)               AS taxa_retencao_pct,
  -- Custo por ThruPlay
  ROUND(SAFE_DIVIDE(SUM(CAST(investimento AS FLOAT64)), SUM(thruplays)), 4) AS custo_thruplay,
  -- Custo por view de 3s
  ROUND(SAFE_DIVIDE(SUM(CAST(investimento AS FLOAT64)), SUM(video_3s)), 4)  AS custo_view_3s,
  -- Hook rate: views 3s / impressões
  ROUND(SAFE_DIVIDE(SUM(video_3s), SUM(impressoes)) * 100, 2)              AS hook_rate_pct
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente    = 'source_of'
  AND plataforma = 'meta'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
  AND ad_name IS NOT NULL
  AND video_3s > 0
GROUP BY campanha, ad_name
HAVING SUM(CAST(investimento AS FLOAT64)) >= 20
ORDER BY thruplays DESC
LIMIT 20;
```

---

## 18. Alerta — campanhas com ROAS baixo

**O que faz:** Lista campanhas com investimento relevante mas ROAS abaixo do threshold definido (padrão: 1.5). São as campanhas que estão queimando budget sem retorno adequado.

**Quando usar:** No monitoramento diário/semanal para identificar campanhas a pausar ou otimizar.

**Como adaptar:** Ajuste o `< 1.5` no HAVING para o threshold de alerta do cliente. Para Source OF, qualquer campanha com ROAS abaixo de 2.0 já merece atenção.

```sql
SELECT
  campanha,
  plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento,
  SUM(compras)                                                              AS compras,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                      AS roas_real,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 1.1215 /
    NULLIF(SUM(compras), 0)
  , 2)                                                                      AS cpa_real,
  MAX(data)                                                                 AS ultimo_dado
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
  AND LOWER(campanha) NOT LIKE '%tráfego%'
  AND LOWER(campanha) NOT LIKE '%awareness%'
GROUP BY campanha, plataforma
HAVING SUM(CAST(investimento AS FLOAT64)) >= 200   -- mínimo de investimento para alertar
   AND SUM(CAST(receita AS FLOAT64)) /
       NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0) < 1.5  -- ← threshold de ROAS
ORDER BY investimento DESC;
```

---

## 19. Alerta — campanhas sem conversão

**O que faz:** Identifica campanhas que estão gastando sem gerar nenhuma compra nos últimos 14 dias. São as campanhas mais urgentes de avaliar.

**Quando usar:** No monitoramento diário — qualquer campanha com mais de R$100 gasto e zero venda precisa de ação imediata.

```sql
SELECT
  campanha,
  plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento,
  SUM(impressoes)                                                           AS impressoes,
  SUM(cliques_link)                                                         AS cliques,
  ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2)           AS ctr_pct,
  MAX(data)                                                                 AS ultimo_dado
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
GROUP BY campanha, plataforma
HAVING SUM(compras) = 0                                    -- zero conversões
   AND SUM(CAST(investimento AS FLOAT64)) >= 100           -- com investimento relevante
ORDER BY investimento DESC;
```

---

## 20. Projeção de fechamento do mês

**O que faz:** Com base no ritmo diário médio dos dias já disponíveis no mês corrente, projeta o investimento, receita e ROAS para o mês completo.

**Quando usar:** A partir do dia 10 do mês, para estimar se o mês vai fechar bem ou se precisa de ajuste de budget.

**Como adaptar:** Troque `'source_of'` pelo cliente desejado.

```sql
WITH dados_mes AS (
  SELECT
    SUM(CAST(investimento AS FLOAT64))  AS invest_real,
    SUM(CAST(receita AS FLOAT64))       AS receita_real,
    SUM(compras)                        AS compras_real,
    COUNT(DISTINCT data)                AS dias_com_dados,
    EXTRACT(DAY FROM DATE_SUB(
      DATE_TRUNC(DATE_ADD(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH),
      INTERVAL 1 DAY
    ))                                  AS dias_no_mes
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente = 'source_of'
    AND DATE_TRUNC(data, MONTH) = DATE_TRUNC(CURRENT_DATE(), MONTH)
)
SELECT
  ROUND(invest_real, 2)                                                     AS invest_ate_agora,
  ROUND(receita_real, 2)                                                    AS receita_ate_agora,
  compras_real                                                              AS compras_ate_agora,
  ROUND(receita_real / NULLIF(invest_real * 1.1215, 0), 2)                 AS roas_ate_agora,
  dias_com_dados                                                            AS dias_com_dados,
  dias_no_mes                                                               AS dias_no_mes,
  ROUND(dias_com_dados * 100.0 / dias_no_mes, 1)                           AS pct_mes_decorrido,
  -- Projeções
  ROUND(invest_real / NULLIF(dias_com_dados, 0) * dias_no_mes, 2)          AS proj_invest_mes,
  ROUND(receita_real / NULLIF(dias_com_dados, 0) * dias_no_mes, 2)         AS proj_receita_mes,
  ROUND(
    (receita_real / NULLIF(dias_com_dados, 0) * dias_no_mes) /
    NULLIF((invest_real / NULLIF(dias_com_dados, 0) * dias_no_mes) * 1.1215, 0)
  , 2)                                                                      AS proj_roas,
  CAST(ROUND(compras_real / NULLIF(dias_com_dados, 0) * dias_no_mes) AS INT64) AS proj_compras
FROM dados_mes;
```

---

## 21. Dois clientes lado a lado

**O que faz:** Compara Source OF e POD Kombucha num único SELECT, com todos os indicadores principais. Ideal para relatório de gestão que precisa ver os dois clientes de uma vez.

**Como adaptar:** Remova o filtro de plataforma para ver todas as plataformas consolidadas por cliente.

```sql
SELECT
  cliente,
  plataforma,
  COUNT(DISTINCT campanha)                                                  AS campanhas,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita,
  SUM(compras)                                                              AS compras,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                      AS roas_real,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 1.1215 /
    NULLIF(SUM(compras), 0)
  , 2)                                                                      AS cpa_real,
  ROUND(SUM(impressoes), 0)                                                 AS impressoes,
  ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2)           AS ctr_pct
FROM `guara-db.guara_performance.ads_diario`
WHERE data BETWEEN '2026-09-01' AND '2026-09-28'  -- sem filtro de cliente
GROUP BY cliente, plataforma
ORDER BY cliente, plataforma;
```

---

## 22. Meta vs Google — comparativo de plataforma

**O que faz:** Compara Meta Ads e Google Ads de um mesmo cliente, mostrando onde o dinheiro rende mais.

**Quando usar:** Para decisões de alocação de budget entre plataformas — responde "onde devo colocar mais verba?".

**Observação:** O fator 1.1215 (imposto) só se aplica ao Meta. O campo `investimento_ajustado` na query já resolve isso.

```sql
SELECT
  plataforma,
  COUNT(DISTINCT campanha)                                                  AS campanhas,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento_bruto,
  ROUND(SUM(CAST(investimento AS FLOAT64)) *
    CASE WHEN plataforma = 'meta' THEN 1.1215 ELSE 1.0 END, 2)             AS investimento_ajustado,
  SUM(compras)                                                              AS compras,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) *
      CASE WHEN plataforma = 'meta' THEN 1.1215 ELSE 1.0 END, 0)
  , 2)                                                                      AS roas_real,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) *
      CASE WHEN plataforma = 'meta' THEN 1.1215 ELSE 1.0 END /
    NULLIF(SUM(compras), 0)
  , 2)                                                                      AS cpa_real,
  ROUND(SUM(impressoes), 0)                                                 AS impressoes,
  ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2)           AS ctr_pct
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY plataforma
ORDER BY plataforma;
```

---
## 23. Ciclo de vida de campanhas

**O que faz:** Mostra para cada campanha: quando começou, quando terminou (ou se ainda está ativa), quantos dias ficou no ar e o investimento total. Dá uma visão de longo prazo do portfólio.

**Quando usar:** Para entender a rotatividade de campanhas — quais foram longevas, quais pausaram cedo e se há campanhas muito antigas ainda ativas.

```sql
SELECT
  campanha,
  plataforma,
  MIN(data)                                                                 AS data_inicio,
  MAX(data)                                                                 AS data_fim,
  DATE_DIFF(MAX(data), MIN(data), DAY) + 1                                  AS dias_ativos,
  CASE
    WHEN MAX(data) >= DATE_SUB(CURRENT_DATE(), INTERVAL 3 DAY) THEN 'Ativa'
    WHEN MAX(data) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY) THEN 'Pausada recentemente'
    ELSE 'Encerrada'
  END                                                                       AS status,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento_total,
  SUM(compras)                                                              AS compras_total,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                      AS roas_vida_toda,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) /
    NULLIF(DATE_DIFF(MAX(data), MIN(data), DAY) + 1, 0)
  , 2)                                                                      AS invest_medio_dia
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente    = 'source_of'
  AND plataforma = 'meta'
GROUP BY campanha, plataforma
ORDER BY investimento_total DESC
LIMIT 30;
```

---

## 24. Ticket médio e pedidos premium (Shopify)

**O que faz:** Analisa a qualidade dos pedidos Shopify — ticket médio real, % de pedidos acima de R$300 e impacto dos descontos na receita.

**Quando usar:** Para monitorar se a loja está crescendo em qualidade (tickets maiores) ou apenas em volume, e avaliar o impacto das promoções.

```sql
SELECT
  FORMAT_DATE('%Y-%m', data)                                                AS mes,
  SUM(pedidos)                                                              AS pedidos,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                   AS receita_liquida,
  ROUND(SUM(CAST(total_desconto AS FLOAT64)), 2)                            AS total_descontos,
  ROUND(
    SUM(CAST(total_desconto AS FLOAT64)) * 100.0 /
    NULLIF(SUM(CAST(receita AS FLOAT64)) + SUM(CAST(total_desconto AS FLOAT64)), 0)
  , 1)                                                                      AS pct_desconto,
  ROUND(SAFE_DIVIDE(SUM(CAST(receita AS FLOAT64)), SUM(pedidos)), 2)        AS ticket_medio_real,
  ROUND(CAST(SUM(CAST(ticket_medio AS FLOAT64)) AS FLOAT64) / NULLIF(COUNT(*), 0), 2) AS ticket_medio_reportado,
  SUM(pedidos_acima_300)                                                    AS pedidos_premium,
  ROUND(SAFE_DIVIDE(SUM(pedidos_acima_300), SUM(pedidos)) * 100, 1)         AS pct_premium,
  SUM(novos_clientes)                                                       AS novos_clientes,
  SUM(recompradores)                                                        AS recompradores,
  ROUND(SAFE_DIVIDE(SUM(recompradores), SUM(pedidos)) * 100, 1)             AS pct_retencao
FROM `guara-db.guara_performance.shopify_diario`
WHERE cliente = 'source_of'
GROUP BY mes
ORDER BY mes DESC
LIMIT 12;
```

---

## 25. Top campanhas por CTR e CPM

**O que faz:** Ranking das campanhas com melhor CTR (engajamento do criativo) e menor CPM (custo de alcance), métricas de topo do funil que indicam a qualidade do criativo e da segmentação.

**Quando usar:** Para identificar quais campanhas têm o criativo mais atrativo — alto CTR + baixo CPM é a combinação ideal para escalar.

**Como adaptar:** Troque `ORDER BY ctr_pct DESC` por `ORDER BY cpm ASC` para priorizar campanhas mais baratas em CPM.

```sql
SELECT
  campanha,
  plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                              AS investimento,
  ROUND(SUM(impressoes), 0)                                                 AS impressoes,
  ROUND(SUM(alcance), 0)                                                    AS alcance,
  ROUND(SUM(cliques_link), 0)                                               AS cliques,
  ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2)           AS ctr_pct,
  ROUND(SAFE_DIVIDE(SUM(CAST(investimento AS FLOAT64)), SUM(impressoes)) * 1000, 2) AS cpm,
  ROUND(SAFE_DIVIDE(SUM(CAST(investimento AS FLOAT64)), SUM(cliques_link)), 2) AS cpc,
  ROUND(SAFE_DIVIDE(SUM(impressoes), SUM(alcance)), 2)                      AS frequencia,
  -- Contexto de conversão
  SUM(compras)                                                              AS compras,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                      AS roas_real
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY campanha, plataforma
HAVING SUM(CAST(investimento AS FLOAT64)) >= 100
   AND SUM(impressoes) > 1000
ORDER BY ctr_pct DESC
LIMIT 20;
```

---

## 📌 Referência rápida — funções BigQuery

| Necessidade | Função | Exemplo |
|---|---|---|
| Truncar para mês | `DATE_TRUNC(data, MONTH)` | `DATE_TRUNC('2026-09-15', MONTH)` → `2026-09-01` |
| Mês como texto | `FORMAT_DATE('%Y-%m', data)` | `'2026-09'` |
| N dias atrás | `DATE_SUB(CURRENT_DATE(), INTERVAL N DAY)` | Últimos 30 dias |
| N meses atrás | `DATE_SUB(CURRENT_DATE(), INTERVAL N MONTH)` | Mês anterior |
| Divisão segura | `SAFE_DIVIDE(a, b)` | Retorna NULL se b = 0 |
| Converter STRING | `CAST(col AS FLOAT64)` | Para colunas monetárias |
| Diferença em dias | `DATE_DIFF(data_fim, data_ini, DAY)` | Número de dias entre datas |
| Dia da semana | `FORMAT_DATE('%A', data)` | `'Monday'` |
| Número da semana | `EXTRACT(WEEK FROM data)` | `39` (semana 39 do ano) |
| Valor anterior (LAG) | `LAG(col) OVER (ORDER BY data)` | Valor do dia anterior |
| Total acumulado | `SUM(col) OVER (ORDER BY data)` | Running total |
| % do total (window) | `SUM(col) OVER ()` | Total geral para calcular share |
| Último dia do mês | `DATE_SUB(DATE_TRUNC(DATE_ADD(data, INTERVAL 1 MONTH), MONTH), INTERVAL 1 DAY)` | Último dia do mês da data |

---

## 📌 Valores fixos de referência

| Variável | Valor | Onde usar |
|---|---|---|
| Fator imposto Meta | `× 1.1215` | investimento Meta × 1.1215 para custo real |
| ROAS alerta crítico | `< 1.0` | Campanha perdendo dinheiro |
| ROAS alerta atenção | `< 1.5` | Campanha abaixo do mínimo aceitável |
| ROAS saudável | `>= 2.0` | Meta Source OF |
| Investimento mínimo p/ análise | `R$ 100` | HAVING threshold |
| Investimento mínimo p/ funil | `R$ 50` | HAVING threshold |
| Frequência de atenção | `>= 3.0` | Início de fadiga |
| Frequência crítica | `>= 3.5` | Fadiga confirmada |
| Cliente Source OF | `'source_of'` | WHERE cliente = |
| Cliente POD Kombucha | `'pod_kombucha'` | WHERE cliente = |
| Plataforma Meta | `'meta'` | WHERE plataforma = |
| Plataforma Google | `'google'` | WHERE plataforma = |

---

## 🔗 Links úteis

| Recurso | Link |
|---|---|
| BigQuery Console | https://console.cloud.google.com/bigquery?project=guara-db |
| Documentação BigQuery SQL | https://cloud.google.com/bigquery/docs/reference/standard-sql/query-syntax |
| Funções de data BQ | https://cloud.google.com/bigquery/docs/reference/standard-sql/date_functions |
| Funções de janela BQ | https://cloud.google.com/bigquery/docs/reference/standard-sql/analytic-function-concepts |

---

*Cesar ⚙️ — Agente de Performance Guará*
