# guara-db · Consultas de Referência

Exemplos prontos para consultar o BigQuery da Guará Media (`guara-db`).  
Projeto: `guara-db` · Região: `US`

---

## Estrutura do projeto

```
guara-db
├── guara_performance          ← performance de mídia paga
│   ├── ads_diario             ← dados brutos Meta + Google Ads (665 k linhas, jan/2024–hoje)
│   ├── shopify_diario         ← receita e pedidos Shopify por dia
│   ├── analise_reunioes       ← análise de sentimento de reuniões (via Claude)
│   ├── v_ads_roas_real        ← view: ROAS com investimento ajustado Meta (×1,1215)
│   ├── v_diario_90d           ← view: últimos 90 dias consolidados
│   ├── v_mensal               ← view: agregado mensal por cliente/plataforma
│   ├── v_benchmark_mensal     ← view: comparativo MoM com variações %
│   ├── v_performance_mensal   ← view: visão completa (ads + shopify + YoY)
│   ├── v_performance_completa ← view: consolidado multi-plataforma por mês
│   ├── v_top_campanhas_mes    ← view: top campanhas do mês corrente
│   └── v_alertas_campanha     ← view: campanhas com ROAS abaixo da média 7d
└── transcricao
    └── transcricao            ← reuniões transcritas do Google Drive
```

**Clientes disponíveis:** `source_of` · `pod_kombucha` · `botica`  
**Plataformas:** `meta` · `google`

---

## 1. `ads_diario` — Dados brutos de mídia

### Performance do mês atual por cliente

```sql
SELECT
  cliente,
  plataforma,
  SUM(investimento)    AS investimento,
  SUM(compras)         AS compras,
  SUM(receita)         AS receita,
  ROUND(SUM(receita) / NULLIF(SUM(investimento), 0), 2) AS roas,
  ROUND(SUM(investimento) / NULLIF(SUM(compras), 0), 2) AS cpa
FROM `guara-db.guara_performance.ads_diario`
WHERE DATE_TRUNC(data, MONTH) = DATE_TRUNC(CURRENT_DATE(), MONTH)
GROUP BY cliente, plataforma
ORDER BY cliente, plataforma;
```

### Série histórica diária de um cliente

```sql
SELECT
  data,
  SUM(investimento) AS investimento,
  SUM(compras)      AS compras,
  SUM(receita)      AS receita,
  ROUND(SUM(receita) / NULLIF(SUM(investimento), 0), 2) AS roas
FROM `guara-db.guara_performance.ads_diario`
WHERE cliente = 'source_of'
  AND data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY data
ORDER BY data;
```

### Top 10 campanhas por receita (últimos 30 dias)

```sql
SELECT
  cliente,
  plataforma,
  campanha,
  SUM(investimento) AS investimento,
  SUM(compras)      AS compras,
  SUM(receita)      AS receita,
  ROUND(SUM(receita) / NULLIF(SUM(investimento), 0), 2) AS roas
FROM `guara-db.guara_performance.ads_diario`
WHERE data >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY cliente, plataforma, campanha
ORDER BY receita DESC
LIMIT 10;
```

### Comparativo entre plataformas (Meta vs Google) no mês

```sql
SELECT
  cliente,
  plataforma,
  SUM(investimento) AS investimento,
  SUM(compras)      AS compras,
  SUM(receita)      AS receita,
  ROUND(AVG(frequencia), 2) AS frequencia_media,
  ROUND(SUM(cliques_link) / NULLIF(SUM(impressoes), 0) * 100, 2) AS ctr_pct
FROM `guara-db.guara_performance.ads_diario`
WHERE DATE_TRUNC(data, MONTH) = DATE_TRUNC(CURRENT_DATE(), MONTH)
GROUP BY cliente, plataforma
ORDER BY cliente, plataforma;
```

### Campanhas ativas hoje

```sql
SELECT
  cliente,
  plataforma,
  campanha,
  status,
  investimento,
  roas
FROM `guara-db.guara_performance.ads_diario`
WHERE data = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
  AND status = 'ACTIVE'
ORDER BY investimento DESC;
```

---

## 2. `shopify_diario` — Receita e pedidos

### Receita mensal por cliente

```sql
SELECT
  cliente,
  DATE_TRUNC(data, MONTH)   AS mes,
  SUM(receita)              AS receita,
  SUM(pedidos)              AS pedidos,
  SUM(novos_clientes)       AS novos_clientes,
  SUM(recompradores)        AS recompradores,
  ROUND(AVG(ticket_medio), 2) AS ticket_medio
FROM `guara-db.guara_performance.shopify_diario`
GROUP BY cliente, mes
ORDER BY cliente, mes DESC;
```

### Taxa de retenção mensal

```sql
SELECT
  cliente,
  DATE_TRUNC(data, MONTH) AS mes,
  SUM(pedidos)            AS pedidos_total,
  SUM(recompradores)      AS recompradores,
  SUM(novos_clientes)     AS novos,
  ROUND(SUM(recompradores) / NULLIF(SUM(pedidos), 0) * 100, 1) AS pct_retencao
FROM `guara-db.guara_performance.shopify_diario`
GROUP BY cliente, mes
ORDER BY cliente, mes DESC;
```

### Pedidos premium (acima de R$ 300) por mês

```sql
SELECT
  cliente,
  DATE_TRUNC(data, MONTH) AS mes,
  SUM(pedidos)            AS pedidos_total,
  SUM(pedidos_acima_300)  AS pedidos_premium,
  ROUND(SUM(pedidos_acima_300) / NULLIF(SUM(pedidos), 0) * 100, 1) AS pct_premium
FROM `guara-db.guara_performance.shopify_diario`
GROUP BY cliente, mes
ORDER BY cliente, mes DESC;
```

---

## 3. `v_ads_roas_real` — ROAS com ajuste de taxa Meta

> Meta cobra em USD. O investimento real em BRL usa multiplicador `×1,1215`.  
> Use esta view para análises de rentabilidade real.

### ROAS real do mês por cliente

```sql
SELECT
  cliente,
  plataforma,
  SUM(investimento)         AS investimento_bruto,
  SUM(investimento_ajustado) AS investimento_real,
  SUM(receita)              AS receita,
  SUM(compras)              AS compras,
  ROUND(AVG(roas_real), 2)  AS roas_real,
  ROUND(AVG(cpa_real), 2)   AS cpa_real
FROM `guara-db.guara_performance.v_ads_roas_real`
WHERE DATE_TRUNC(data, MONTH) = DATE_TRUNC(CURRENT_DATE(), MONTH)
GROUP BY cliente, plataforma
ORDER BY cliente;
```

---

## 4. `v_diario_90d` — Últimos 90 dias

### Evolução diária de ROAS real

```sql
SELECT
  data,
  cliente,
  plataforma,
  investimento_ajustado,
  receita,
  roas_real,
  ROUND(cliques_link / NULLIF(impressoes, 0) * 100, 2) AS ctr_pct
FROM `guara-db.guara_performance.v_diario_90d`
WHERE cliente = 'source_of'
ORDER BY data DESC;
```

### Média móvel 7 dias de ROAS

```sql
SELECT
  data,
  cliente,
  roas_real,
  ROUND(AVG(roas_real) OVER (
    PARTITION BY cliente
    ORDER BY data
    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
  ), 2) AS roas_media_7d
FROM `guara-db.guara_performance.v_diario_90d`
ORDER BY cliente, data;
```

---

## 5. `v_benchmark_mensal` — Comparativo MoM

### Variação mês a mês

```sql
SELECT
  cliente,
  plataforma,
  FORMAT_DATE('%Y-%m', mes)   AS mes,
  investimento_ajustado,
  receita,
  roas_real,
  CONCAT(CAST(ROUND(variacao_roas_pct, 1) AS STRING), '%')    AS var_roas,
  CONCAT(CAST(ROUND(variacao_receita_pct, 1) AS STRING), '%') AS var_receita
FROM `guara-db.guara_performance.v_benchmark_mensal`
ORDER BY cliente, mes DESC
LIMIT 24;
```

---

## 6. `v_performance_mensal` — Visão completa com YoY

### Dashboard mensal consolidado

```sql
SELECT
  cliente,
  FORMAT_DATE('%Y-%m', mes)  AS mes,
  investimento_total,
  receita_shopify,
  roas_real,
  cpa,
  pedidos,
  ticket_medio,
  ROUND(pct_retencao * 100, 1) AS pct_retencao,
  ROUND(var_receita_pct, 1)    AS var_receita_mom,
  ROUND(var_roas_yoy_pct, 1)   AS var_roas_yoy
FROM `guara-db.guara_performance.v_performance_mensal`
ORDER BY cliente, mes DESC;
```

### Comparativo YoY (ano anterior vs atual)

```sql
SELECT
  cliente,
  FORMAT_DATE('%Y-%m', mes) AS mes,
  receita_shopify           AS receita_atual,
  receita_yoy               AS receita_ano_ant,
  ROUND(var_receita_yoy_pct, 1) AS variacao_yoy_pct,
  roas_real                 AS roas_atual,
  roas_yoy                  AS roas_ano_ant,
  ROUND(var_roas_yoy_pct, 1) AS variacao_roas_yoy_pct
FROM `guara-db.guara_performance.v_performance_mensal`
WHERE receita_yoy IS NOT NULL
ORDER BY cliente, mes DESC;
```

---

## 7. `v_top_campanhas_mes` — Melhores campanhas do mês

```sql
SELECT
  cliente,
  plataforma,
  campanha,
  investimento_ajustado,
  receita,
  compras,
  roas_real,
  ROUND(ctr * 100, 2) AS ctr_pct
FROM `guara-db.guara_performance.v_top_campanhas_mes`
ORDER BY roas_real DESC
LIMIT 20;
```

---

## 8. `v_alertas_campanha` — Campanhas abaixo da média

> Campanhas com ROAS do dia abaixo da média dos últimos 7 dias.

```sql
SELECT
  cliente,
  plataforma,
  campanha,
  data,
  investimento,
  roas,
  roas_media_7d,
  status_alerta
FROM `guara-db.guara_performance.v_alertas_campanha`
WHERE status_alerta = 'ABAIXO_MEDIA'
ORDER BY data DESC, investimento DESC;
```

---

## 9. `transcricao.transcricao` — Reuniões transcritas

### Reuniões recentes

```sql
SELECT
  data,
  hora,
  titulo,
  participantes,
  resumo,
  proximas_etapas
FROM `guara-db.transcricao.transcricao`
ORDER BY data DESC, criado_em DESC
LIMIT 20;
```

### Busca por palavra-chave no conteúdo

```sql
SELECT
  data,
  titulo,
  resumo,
  decisoes
FROM `guara-db.transcricao.transcricao`
WHERE LOWER(detalhes) LIKE '%source of%'
   OR LOWER(detalhes) LIKE '%pod kombucha%'
ORDER BY data DESC;
```

### Próximas etapas de todas as reuniões

```sql
SELECT
  data,
  titulo,
  proximas_etapas
FROM `guara-db.transcricao.transcricao`
WHERE proximas_etapas IS NOT NULL
  AND proximas_etapas != ''
ORDER BY data DESC;
```

---

## 10. `analise_reunioes` — Sentimento e alertas (IA)

### Reuniões com alertas críticos

```sql
SELECT
  data,
  titulo,
  sentimento,
  alertas,
  resumo_analise,
  analisado_em
FROM `guara-db.guara_performance.analise_reunioes`
WHERE JSON_VALUE(alertas, '$[0].tipo') IS NOT NULL
ORDER BY data DESC;
```

### Alertas por tipo

```sql
SELECT
  alerta.tipo,
  alerta.cliente,
  alerta.descricao,
  r.titulo,
  r.data
FROM `guara-db.guara_performance.analise_reunioes` r,
  UNNEST(JSON_QUERY_ARRAY(alertas)) AS alerta_json,
  UNNEST([STRUCT(
    JSON_VALUE(alerta_json, '$.tipo')      AS tipo,
    JSON_VALUE(alerta_json, '$.cliente')   AS cliente,
    JSON_VALUE(alerta_json, '$.descricao') AS descricao
  )]) AS alerta
WHERE alerta.tipo IN ('urgente', 'sem_resposta', 'sem_acesso', 'bloqueio')
ORDER BY r.data DESC;
```

---

## Consultas cruzadas (ads + shopify + reuniões)

### ROAS real vs receita Shopify — visão unificada por mês

```sql
SELECT
  p.cliente,
  FORMAT_DATE('%Y-%m', p.mes)   AS mes,
  p.investimento_total,
  p.receita_shopify,
  p.roas_real,
  p.cpa,
  p.pedidos,
  p.ticket_medio,
  p.novos_clientes,
  p.recompradores
FROM `guara-db.guara_performance.v_performance_completa` p
ORDER BY p.cliente, p.mes DESC;
```

### Clientes mencionados em reuniões com performance do mês

```sql
WITH reunioes_clientes AS (
  SELECT DISTINCT
    LOWER(JSON_VALUE(cliente_json)) AS cliente_reuniao,
    DATE_TRUNC(data, MONTH)         AS mes
  FROM `guara-db.guara_performance.analise_reunioes`,
    UNNEST(JSON_QUERY_ARRAY(clientes_mencionados)) AS cliente_json
)
SELECT
  rc.cliente_reuniao,
  FORMAT_DATE('%Y-%m', m.mes) AS mes,
  m.investimento_total,
  m.receita_shopify,
  m.roas_real
FROM reunioes_clientes rc
LEFT JOIN `guara-db.guara_performance.v_performance_completa` m
  ON LOWER(m.cliente) LIKE CONCAT('%', rc.cliente_reuniao, '%')
  AND m.mes = rc.mes
ORDER BY rc.mes DESC;
```

---

## Dicas rápidas

```sql
-- Clientes disponíveis
SELECT DISTINCT cliente FROM `guara-db.guara_performance.ads_diario` ORDER BY cliente;

-- Período disponível
SELECT MIN(data) AS inicio, MAX(data) AS fim
FROM `guara-db.guara_performance.ads_diario`;

-- Checar dados do dia anterior
SELECT cliente, plataforma, SUM(investimento) AS invest, SUM(compras) AS compras
FROM `guara-db.guara_performance.ads_diario`
WHERE data = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
GROUP BY cliente, plataforma;
```

---

*Gerado automaticamente pelo Agente de Performance Guará · atualizado em 2026-10-06*
