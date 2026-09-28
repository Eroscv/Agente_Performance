# 📊 Guia BigQuery — Agente de Performance Guará

> Cole e execute direto no console em: https://console.cloud.google.com/bigquery  
> Projeto: **`guara-db`** | Dataset: **`guara_performance`**  
> Cesar ⚙️ — atualizado em 28/09/2026

---

## 🗂️ Referência rápida

| Objeto | Nome completo | Conteúdo |
|---|---|---|
| Tabela | `guara-db.guara_performance.ads_diario` | Meta Ads + Google Ads (todos os clientes) |
| Tabela | `guara-db.guara_performance.shopify_diario` | Receita diária Shopify |
| View | `guara-db.guara_performance.v_performance_mensal` | Consolidado mensal com ROAS real, MoM e YoY |

**Clientes disponíveis:** `source_of` · `pod_kombucha`  
**Plataformas:** `meta` · `google`

---

## ⚠️ Regras antes de consultar

1. **Sempre filtrar por `cliente`** — nunca consultar sem `WHERE cliente = '...'`
2. **Colunas monetárias são STRING** — sempre fazer `CAST(investimento AS FLOAT64)` e `CAST(receita AS FLOAT64)`
3. **ROAS ponderado** — sempre `SUM(CAST(receita AS FLOAT64)) / NULLIF(SUM(CAST(investimento AS FLOAT64)), 0)`, nunca `AVG(roas)`
4. **Fator de imposto Meta** — para ROAS e CPA reais, multiplicar investimento Meta por `1.1215`
5. **Datas no formato ISO** — `'2026-09-01'` (DATE literal no BQ)

---

## 1. Diagnóstico — ver o que tem no banco

```sql
-- ✅ Verificar cobertura de datas por cliente e plataforma
SELECT
  cliente,
  plataforma,
  MIN(data)      AS primeiro_dia,
  MAX(data)      AS ultimo_dia,
  COUNT(*)       AS total_linhas,
  COUNT(DISTINCT campanha) AS total_campanhas
FROM `guara-db.guara_performance.ads_diario`
GROUP BY cliente, plataforma
ORDER BY cliente, plataforma;
```

```sql
-- ✅ Verificar Shopify
SELECT
  cliente,
  MIN(data)          AS primeiro_dia,
  MAX(data)          AS ultimo_dia,
  COUNT(*)           AS total_dias,
  SUM(CAST(receita AS FLOAT64)) AS receita_total
FROM `guara-db.guara_performance.shopify_diario`
GROUP BY cliente
ORDER BY cliente;
```

---

## 2. Resumo de período

```sql
-- ✅ Totais do mês de setembro/2026 — Source OF
SELECT
  COUNT(DISTINCT campanha)                                               AS campanhas_ativas,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                           AS investimento_total,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                AS receita_total,
  SUM(compras)                                                           AS compras_total,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)), 0)
  , 2)                                                                   AS roas_bruto,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                   AS roas_real,   -- com imposto Meta
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 1.1215 /
    NULLIF(SUM(compras), 0)
  , 2)                                                                   AS cpa_real,
  ROUND(SUM(impressoes), 0)                                              AS impressoes,
  ROUND(
    SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100
  , 2)                                                                   AS ctr_pct
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente   = 'source_of'
  AND plataforma = 'meta'
  AND data BETWEEN '2026-09-01' AND '2026-09-28';
```

---

## 3. Ranking de campanhas por ROAS

```sql
-- ✅ Top 15 campanhas por ROAS real — setembro/2026
SELECT
  campanha,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                             AS investimento,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                                  AS receita,
  SUM(compras)                                                             AS compras,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                     AS roas_real,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 1.1215 /
    NULLIF(SUM(compras), 0)
  , 2)                                                                     AS cpa_real
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente    = 'source_of'
  AND plataforma = 'meta'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
  AND LOWER(campanha) NOT LIKE '%tráfego%'
  AND LOWER(campanha) NOT LIKE '%awareness%'
GROUP BY campanha
HAVING SUM(CAST(investimento AS FLOAT64)) >= 100
ORDER BY roas_real DESC
LIMIT 15;
```

---

## 4. Comparativo temporal — semana a semana

```sql
-- ✅ Comparativo: semana atual vs semana anterior
WITH semana_atual AS (
  SELECT
    campanha,
    SUM(CAST(investimento AS FLOAT64)) AS inv_a,
    SUM(CAST(receita AS FLOAT64))      AS rec_a,
    SUM(compras)                       AS comp_a
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente    = 'source_of'
    AND plataforma = 'meta'
    AND data BETWEEN '2026-09-22' AND '2026-09-28'
  GROUP BY campanha
),
semana_anterior AS (
  SELECT
    campanha,
    SUM(CAST(investimento AS FLOAT64)) AS inv_b,
    SUM(CAST(receita AS FLOAT64))      AS rec_b,
    SUM(compras)                       AS comp_b
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente    = 'source_of'
    AND plataforma = 'meta'
    AND data BETWEEN '2026-09-15' AND '2026-09-21'
  GROUP BY campanha
)
SELECT
  a.campanha,
  ROUND(a.rec_a / NULLIF(a.inv_a * 1.1215, 0), 2)         AS roas_atual,
  ROUND(b.rec_b / NULLIF(b.inv_b * 1.1215, 0), 2)         AS roas_anterior,
  ROUND(
    (a.rec_a / NULLIF(a.inv_a * 1.1215, 0)) -
    (b.rec_b / NULLIF(b.inv_b * 1.1215, 0))
  , 2)                                                     AS delta_roas,
  ROUND(a.inv_a, 2)                                        AS invest_atual,
  ROUND(b.inv_b, 2)                                        AS invest_anterior,
  ROUND(
    SAFE_DIVIDE(a.inv_a - b.inv_b, b.inv_b) * 100
  , 1)                                                     AS var_invest_pct
FROM semana_atual a
LEFT JOIN semana_anterior b USING (campanha)
ORDER BY roas_atual DESC
LIMIT 20;
```

---

## 5. Comparativo mensal — MoM

```sql
-- ✅ Mês atual vs mês anterior (Source OF)
WITH mes_atual AS (
  SELECT
    SUM(CAST(investimento AS FLOAT64)) AS inv,
    SUM(CAST(receita AS FLOAT64))      AS rec,
    SUM(compras)                       AS comp,
    SUM(impressoes)                    AS imp
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente = 'source_of'
    AND DATE_TRUNC(data, MONTH) = DATE_TRUNC(CURRENT_DATE(), MONTH)
),
mes_anterior AS (
  SELECT
    SUM(CAST(investimento AS FLOAT64)) AS inv,
    SUM(CAST(receita AS FLOAT64))      AS rec,
    SUM(compras)                       AS comp,
    SUM(impressoes)                    AS imp
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente = 'source_of'
    AND DATE_TRUNC(data, MONTH) = DATE_TRUNC(DATE_SUB(CURRENT_DATE(), INTERVAL 1 MONTH), MONTH)
)
SELECT
  ROUND(a.inv, 2)                                            AS invest_atual,
  ROUND(b.inv, 2)                                            AS invest_anterior,
  ROUND(SAFE_DIVIDE(a.inv - b.inv, b.inv) * 100, 1)         AS var_invest_pct,
  ROUND(a.rec / NULLIF(a.inv * 1.1215, 0), 2)               AS roas_atual,
  ROUND(b.rec / NULLIF(b.inv * 1.1215, 0), 2)               AS roas_anterior,
  ROUND(
    (a.rec / NULLIF(a.inv * 1.1215, 0)) -
    (b.rec / NULLIF(b.inv * 1.1215, 0))
  , 2)                                                       AS delta_roas,
  a.comp                                                     AS compras_atual,
  b.comp                                                     AS compras_anterior,
  ROUND(SAFE_DIVIDE(a.comp - b.comp, b.comp) * 100, 1)      AS var_compras_pct
FROM mes_atual a, mes_anterior b;
```

---

## 6. View pronta — `v_performance_mensal`

A view já cruza ads + Shopify e calcula ROAS real, CPA, retenção e variações MoM/YoY. **Use ela para análises mensais.**

```sql
-- ✅ Histórico mensal completo — Source OF
SELECT
  FORMAT_DATE('%Y-%m', mes)  AS mes,
  investimento_meta,
  investimento_google,
  investimento_total,
  receita_shopify,
  roas_real,           -- receita_shopify / investimento_total (meta já com ×1.1215)
  cpa,
  compras_total,
  pct_retencao,        -- % recompra
  pct_novos,           -- % novos clientes
  var_receita_pct,     -- variação MoM receita
  var_roas_pct,        -- variação MoM ROAS
  var_receita_yoy_pct  -- variação YoY receita
FROM `guara-db.guara_performance.v_performance_mensal`
WHERE cliente = 'source_of'
ORDER BY mes DESC
LIMIT 12;
```

```sql
-- ✅ Alertas: meses com ROAS abaixo de 2.0
SELECT
  FORMAT_DATE('%Y-%m', mes) AS mes,
  cliente,
  roas_real,
  investimento_total,
  receita_shopify,
  var_roas_pct
FROM `guara-db.guara_performance.v_performance_mensal`
WHERE roas_real < 2.0
  AND investimento_total > 1000
ORDER BY mes DESC, roas_real ASC;
```

---

## 7. Shopify — receita real

```sql
-- ✅ Receita mensal Shopify — Source OF
SELECT
  FORMAT_DATE('%Y-%m', data)           AS mes,
  SUM(CAST(receita AS FLOAT64))        AS receita,
  SUM(pedidos)                         AS pedidos,
  SUM(novos_clientes)                  AS novos,
  SUM(recompradores)                   AS recompradores,
  ROUND(
    SAFE_DIVIDE(SUM(recompradores), SUM(pedidos)) * 100
  , 1)                                 AS pct_retencao,
  ROUND(
    SAFE_DIVIDE(SUM(CAST(receita AS FLOAT64)), SUM(pedidos))
  , 2)                                 AS ticket_medio_real,
  SUM(pedidos_acima_300)               AS pedidos_premium
FROM `guara-db.guara_performance.shopify_diario`
WHERE cliente = 'source_of'
GROUP BY mes
ORDER BY mes DESC
LIMIT 12;
```

```sql
-- ✅ ROAS real cruzando Shopify + Meta Ads (join manual)
WITH ads AS (
  SELECT
    DATE_TRUNC(data, MONTH)            AS mes,
    SUM(CAST(investimento AS FLOAT64)) AS invest_meta
  FROM `guara-db.guara_performance.ads_diario`
  WHERE cliente    = 'source_of'
    AND plataforma = 'meta'
  GROUP BY mes
),
shopify AS (
  SELECT
    DATE_TRUNC(data, MONTH)           AS mes,
    SUM(CAST(receita AS FLOAT64))     AS receita_shopify
  FROM `guara-db.guara_performance.shopify_diario`
  WHERE cliente = 'source_of'
  GROUP BY mes
)
SELECT
  FORMAT_DATE('%Y-%m', a.mes)                                    AS mes,
  ROUND(a.invest_meta, 2)                                        AS invest_meta,
  ROUND(a.invest_meta * 1.1215, 2)                               AS invest_com_imposto,
  ROUND(s.receita_shopify, 2)                                    AS receita_shopify,
  ROUND(
    s.receita_shopify / NULLIF(a.invest_meta * 1.1215, 0)
  , 2)                                                           AS roas_real
FROM ads a
JOIN shopify s USING (mes)
ORDER BY mes DESC
LIMIT 12;
```

---

## 8. Análise de criativos (nível de anúncio — Meta only)

```sql
-- ✅ Top anúncios por ROAS — setembro/2026
SELECT
  ad_name,
  adset_name,
  campanha,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                    AS investimento,
  SUM(compras)                                                    AS compras,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                         AS receita,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                            AS roas_real,
  ROUND(SUM(impressoes), 0)                                       AS impressoes,
  ROUND(SAFE_DIVIDE(SUM(cliques_link), SUM(impressoes)) * 100, 2) AS ctr_pct,
  ROUND(SAFE_DIVIDE(SUM(CAST(investimento AS FLOAT64)), SUM(impressoes)) * 1000, 2) AS cpm
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente    = 'source_of'
  AND plataforma = 'meta'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
  AND ad_name IS NOT NULL
GROUP BY ad_name, adset_name, campanha
HAVING SUM(CAST(investimento AS FLOAT64)) >= 50
ORDER BY roas_real DESC
LIMIT 20;
```

---

## 9. Distribuição de budget

```sql
-- ✅ Share de investimento por campanha — setembro/2026
SELECT
  campanha,
  plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                        AS investimento,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 100.0 /
    NULLIF(SUM(SUM(CAST(investimento AS FLOAT64))) OVER (), 0)
  , 1)                                                                AS share_pct,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                                AS roas_real
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY campanha, plataforma
ORDER BY investimento DESC
LIMIT 20;
```

---

## 10. Campanhas problemáticas — alertas

```sql
-- ✅ Campanhas com gasto alto e ROAS baixo (últimos 30 dias)
SELECT
  campanha,
  plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                    AS investimento,
  SUM(compras)                                                    AS compras,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                            AS roas_real
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY campanha, plataforma
HAVING SUM(CAST(investimento AS FLOAT64)) >= 200
   AND SUM(CAST(receita AS FLOAT64)) / NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0) < 1.5
ORDER BY investimento DESC
LIMIT 15;
```

```sql
-- ✅ Campanhas ativas com zero conversão (últimos 14 dias)
SELECT
  campanha,
  plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2) AS investimento,
  SUM(impressoes)                              AS impressoes,
  MAX(data)                                    AS ultimo_dado
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
GROUP BY campanha, plataforma
HAVING SUM(compras) = 0
   AND SUM(CAST(investimento AS FLOAT64)) >= 100
ORDER BY investimento DESC;
```

---

## 11. Série diária — acompanhamento dia a dia

```sql
-- ✅ Série diária de setembro/2026 — Source OF (todas as plataformas)
SELECT
  data,
  plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                    AS investimento,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                         AS receita,
  SUM(compras)                                                    AS compras,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                            AS roas_real,
  COUNT(DISTINCT campanha)                                        AS campanhas_ativas
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY data, plataforma
ORDER BY data DESC, plataforma;
```

---

## 12. Projeção de fechamento do mês

```sql
-- ✅ Projeção para o mês corrente (ritmo dos dias disponíveis)
WITH dias_disponiveis AS (
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
  ROUND(invest_real, 2)                                               AS invest_ate_agora,
  ROUND(receita_real, 2)                                              AS receita_ate_agora,
  compras_real                                                        AS compras_ate_agora,
  dias_com_dados                                                      AS dias_com_dados,
  dias_no_mes                                                         AS dias_no_mes,
  ROUND(invest_real / NULLIF(dias_com_dados, 0) * dias_no_mes, 2)    AS proj_invest_mes,
  ROUND(receita_real / NULLIF(dias_com_dados, 0) * dias_no_mes, 2)   AS proj_receita_mes,
  ROUND(
    (receita_real / NULLIF(dias_com_dados, 0) * dias_no_mes) /
    NULLIF((invest_real / NULLIF(dias_com_dados, 0) * dias_no_mes) * 1.1215, 0)
  , 2)                                                                AS proj_roas
FROM dias_disponiveis;
```

---

## 13. Comparativo dos dois clientes lado a lado

```sql
-- ✅ Source OF vs POD Kombucha — setembro/2026
SELECT
  cliente,
  plataforma,
  ROUND(SUM(CAST(investimento AS FLOAT64)), 2)                    AS investimento,
  ROUND(SUM(CAST(receita AS FLOAT64)), 2)                         AS receita,
  SUM(compras)                                                    AS compras,
  ROUND(
    SUM(CAST(receita AS FLOAT64)) /
    NULLIF(SUM(CAST(investimento AS FLOAT64)) * 1.1215, 0)
  , 2)                                                            AS roas_real,
  ROUND(
    SUM(CAST(investimento AS FLOAT64)) * 1.1215 /
    NULLIF(SUM(compras), 0)
  , 2)                                                            AS cpa_real,
  COUNT(DISTINCT campanha)                                        AS campanhas
FROM `guara-db.guara_performance.ads_diario`
WHERE data BETWEEN '2026-09-01' AND '2026-09-28'
GROUP BY cliente, plataforma
ORDER BY cliente, plataforma;
```

---

## 📌 Referência rápida — funções BQ mais usadas

| Necessidade | Função BigQuery |
|---|---|
| Truncar para mês | `DATE_TRUNC(data, MONTH)` |
| Mês como texto | `FORMAT_DATE('%Y-%m', data)` |
| N dias atrás | `DATE_SUB(CURRENT_DATE(), INTERVAL N DAY)` |
| Divisão segura (sem erro) | `SAFE_DIVIDE(numerador, denominador)` |
| Converter STRING → número | `CAST(investimento AS FLOAT64)` |
| Último dia do mês | `DATE_SUB(DATE_TRUNC(DATE_ADD(data, INTERVAL 1 MONTH), MONTH), INTERVAL 1 DAY)` |
| Window function | `SUM(...) OVER (PARTITION BY cliente ORDER BY mes)` |

---

## 📌 Lembrete de valores fixos

| Variável | Valor |
|---|---|
| Fator imposto Meta | `× 1.1215` |
| ROAS mínimo aceitável | `1.5` (alerta abaixo disso) |
| Investimento mínimo para análise | `R$ 100` |
| Cliente Source OF | `'source_of'` |
| Cliente POD Kombucha | `'pod_kombucha'` |
| Plataforma Meta | `'meta'` |
| Plataforma Google | `'google'` |

---

*Cesar ⚙️ — Agente de Performance Guará*
