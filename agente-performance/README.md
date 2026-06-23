# Agente de Performance Guará ⚙️

API de consulta inteligente a dados de campanhas Meta Ads (Facebook/Instagram).
Suporta **linguagem natural**, **SQL livre** e **endpoints REST estruturados**.

---

## Sumário

- [Arquitetura](#arquitetura)
- [Instalação e Setup](#instalação-e-setup)
- [Como as Consultas Funcionam](#como-as-consultas-funcionam)
- [1. Endpoint de Linguagem Natural — `/consulta`](#1-endpoint-de-linguagem-natural----consulta)
- [2. Endpoint SQL Livre — `/sql`](#2-endpoint-sql-livre----sql)
- [3. Endpoints REST Estruturados](#3-endpoints-rest-estruturados)
- [Dicionário de Métricas](#dicionário-de-métricas)

---

## Arquitetura

```
data/campanhas.csv   ←  fonte (30.905 registros | 93 colunas | jan/2024 – jun/2026)
        ↓
data/campanhas.db    ←  SQLite (gerado pelo loader)
        ↓
db/connection.py     ←  conexão + helpers query()
db/queries.py        ←  10 consultas SQL prontas
agent/engine.py      ←  detecção de intenção (NLP leve) + roteamento
api/app.py           ←  FastAPI REST (12 endpoints)
```

### Fluxo de uma consulta natural

```
Pergunta em PT-BR
      ↓
engine._detect_intent()   →  identifica intenção (ex: top_roas)
engine._extract_dates()   →  extrai período (ex: últimos 30 dias)
engine._extract_campaign()→  identifica campanha mencionada
      ↓
queries.<função>()        →  executa SQL no SQLite
      ↓
{ intent, dados, meta }   →  resposta estruturada
```

---

## Instalação e Setup

```bash
# 1. Instalar dependências
cd agente-performance
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Carregar o CSV no banco SQLite
python3 -m db.loader
# ✅ 30905 linhas carregadas em data/campanhas.db

# 3. Subir a API
python3 -m api.app
# Uvicorn rodando em http://0.0.0.0:8001
```

> **Porta padrão:** 8001 (configurável via variável `PORT` no `.env`)

---

## Como as Consultas Funcionam

O agente oferece **3 formas** de consultar os dados:

| Forma | Endpoint | Melhor para |
|-------|----------|-------------|
| Linguagem natural | `POST /consulta` | Perguntas em PT-BR sem precisar saber SQL |
| SQL livre | `POST /sql` | Análises avançadas e personalizadas |
| REST estruturado | `GET /resumo`, `/top/roas`, etc. | Integrações, dashboards, automações |

---

## 1. Endpoint de Linguagem Natural — `/consulta`

**`POST /consulta`**

Envie uma pergunta em português. O agente detecta a intenção, extrai datas e nome de campanha automaticamente.

### Resposta padrão

```json
{
  "intent": "top_roas",
  "dados": [ ... ],
  "meta": {
    "intent": "top_roas",
    "campanha": null,
    "data_inicio": "2026-05-24",
    "data_fim": "2026-06-23",
    "limit": 10
  }
}
```

### Intenções suportadas

| Intenção | Palavras-chave detectadas |
|----------|--------------------------|
| `listar_campanhas` | listar, quais campanhas, mostra campanhas |
| `top_roas` | melhor ROAS, maior ROAS, top ROAS, mais eficiente |
| `top_compras` | mais compras, maior compras, top compras |
| `alertas_roas` | alerta, ROAS baixo, ROAS ruim, problema |
| `funil` | funil, carrinho, checkout, conversão |
| `evolucao_diaria` | evolução, diário, ao longo, por dia, histórico |
| `comparar` | comparar, versus, vs, lado a lado |
| `performance_campanha` | performance, desempenho, resultado |
| `periodo` | período, resumo de, total de, consolidado |
| `resumo_geral` | resumo, geral, overview, visão geral, como está |

---

### 📋 20 Exemplos — Linguagem Natural

#### Listagem e Visão Geral

**1. Listar todas as campanhas**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "quais campanhas estão ativas?"}'
```

**2. Visão geral do desempenho**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "como estão as campanhas?"}'
```

**3. Resumo geral de todos os investimentos**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "visão geral de todas as campanhas com investimento total"}'
```

**4. Listar campanhas com maior investimento**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "quais campanhas tiveram maior investimento?"}'
```

**5. Resumo consolidado do mês atual**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "resumo consolidado desse mês"}'
```

---

#### ROAS e Eficiência

**6. Top 5 campanhas por ROAS**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "top 5 campanhas por ROAS"}'
```

**7. Melhor ROAS nos últimos 30 dias**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "qual o melhor ROAS dos últimos 30 dias?"}'
```

**8. Campanhas mais eficientes da semana passada**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "campanhas mais eficientes da semana passada"}'
```

**9. Top 10 ROAS do último trimestre**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "top 10 campanhas por ROAS no último trimestre"}'
```

**10. Alertas de ROAS abaixo do esperado**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "quais campanhas estão com ROAS baixo?"}'
```

---

#### Compras e Receita

**11. Campanhas com mais compras**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "quais campanhas geraram mais compras?"}'
```

**12. Top 5 em volume de compras nos últimos 7 dias**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "top 5 maior volume de compras nos últimos 7 dias"}'
```

**13. Performance de compras da campanha ABO**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "desempenho de compras da ABO_Testes_Todos_Produtos"}'
```

**14. Resultado de compras esse mês**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "total de compras esse mês por campanha"}'
```

**15. Campanha com maior receita gerada**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "qual campanha gerou mais receita?"}'
```

---

#### Funil e Conversão

**16. Funil completo geral**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "mostre o funil de conversão completo"}'
```

**17. Funil da campanha Advantage Hidratante**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "funil de conversão da Advantage_HidratanteCorporal"}'
```

**18. Abandono de carrinho nos últimos 30 dias**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "taxa de abandono de carrinho e checkout nos últimos 30 dias"}'
```

---

#### Evolução e Histórico

**19. Evolução diária da campanha principal**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "evolução diária da ABO_Testes_Todos_Produtos nos últimos 7 dias"}'
```

**20. Histórico de investimento diário geral**
```bash
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "histórico diário de investimento dos últimos 30 dias"}'
```

---

## 2. Endpoint SQL Livre — `/sql`

**`POST /sql`**

Executa qualquer `SELECT` diretamente no banco SQLite.
Apenas leitura — comandos `INSERT`, `UPDATE`, `DELETE` são bloqueados.

### Tabela: `campanhas`

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `data_inicio` | DATE | Data de início do período |
| `data_fim` | DATE | Data de fim do período |
| `campanha` | TEXT | Nome da campanha |
| `investimento` | REAL | Valor gasto (R$) |
| `valor_compras` | REAL | Receita gerada (R$) |
| `compras` | REAL | Número de compras |
| `roas_compras` | REAL | ROAS das compras |
| `impressoes` | INTEGER | Total de impressões |
| `alcance` | INTEGER | Pessoas alcançadas |
| `cliques_link` | INTEGER | Cliques no link |
| `ctr_link` | REAL | CTR do link (%) |
| `cpc_link` | REAL | CPC do link (R$) |
| `cpm` | REAL | CPM (R$) |
| `frequencia` | REAL | Frequência média |
| `adicoes_carrinho` | INTEGER | Adições ao carrinho |
| `checkout_iniciado` | INTEGER | Checkouts iniciados |
| `custo_por_compra` | REAL | Custo por compra (R$) |
| `views_conteudo` | INTEGER | Visualizações de conteúdo |
| `visualizacoes_landing` | INTEGER | Views da landing page |
| `engajamentos_post` | INTEGER | Engajamentos com o post |

> Ver lista completa das 93 colunas em `db/schema.py`

---

### 📋 20 Exemplos — SQL Livre

**1. Total investido e receita por campanha (all time)**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, ROUND(SUM(investimento),2) AS investimento, ROUND(SUM(valor_compras),2) AS receita, ROUND(SUM(valor_compras)/NULLIF(SUM(investimento),0),2) AS roas FROM campanhas GROUP BY campanha ORDER BY investimento DESC"
  }'
```

**2. Desempenho mensal agregado**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT strftime('"'"'%Y-%m'"'"', data_inicio) AS mes, ROUND(SUM(investimento),2) AS investimento, ROUND(SUM(valor_compras),2) AS receita, ROUND(SUM(compras),0) AS compras FROM campanhas GROUP BY mes ORDER BY mes"
  }'
```

**3. Dias com maior investimento**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT data_inicio, campanha, ROUND(investimento,2) AS investimento FROM campanhas ORDER BY investimento DESC LIMIT 20"
  }'
```

**4. Campanhas com ROAS acima de 3.0**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, ROUND(AVG(roas_compras),2) AS roas_medio, ROUND(SUM(investimento),2) AS investimento FROM campanhas GROUP BY campanha HAVING roas_medio > 3.0 ORDER BY roas_medio DESC"
  }'
```

**5. CPC médio por campanha**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, ROUND(AVG(cpc_link),2) AS cpc_medio, ROUND(AVG(ctr_link),4) AS ctr_medio, SUM(cliques_link) AS cliques_total FROM campanhas GROUP BY campanha ORDER BY cpc_medio ASC"
  }'
```

**6. Taxa de conversão clique → compra**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, SUM(cliques_link) AS cliques, SUM(compras) AS compras, ROUND(CAST(SUM(compras) AS REAL)/NULLIF(SUM(cliques_link),0)*100, 2) AS taxa_conversao_pct FROM campanhas GROUP BY campanha HAVING cliques > 0 ORDER BY taxa_conversao_pct DESC"
  }'
```

**7. Funil completo por campanha**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, SUM(impressoes) AS impressoes, SUM(cliques_link) AS cliques, SUM(adicoes_carrinho) AS carrinho, SUM(checkout_iniciado) AS checkout, ROUND(SUM(compras),0) AS compras FROM campanhas GROUP BY campanha ORDER BY impressoes DESC"
  }'
```

**8. Análise de frequência por campanha**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, ROUND(AVG(frequencia),2) AS frequencia_media, ROUND(MIN(frequencia),2) AS freq_min, ROUND(MAX(frequencia),2) AS freq_max FROM campanhas WHERE frequencia > 0 GROUP BY campanha ORDER BY frequencia_media DESC"
  }'
```

**9. Performance semana a semana**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT strftime('"'"'%Y-W%W'"'"', data_inicio) AS semana, ROUND(SUM(investimento),2) AS investimento, ROUND(SUM(valor_compras),2) AS receita, ROUND(SUM(compras),0) AS compras FROM campanhas GROUP BY semana ORDER BY semana DESC LIMIT 12"
  }'
```

**10. Campanhas ativas em 2026**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT DISTINCT campanha, MIN(data_inicio) AS inicio, MAX(data_fim) AS fim, ROUND(SUM(investimento),2) AS investimento FROM campanhas WHERE data_inicio >= '"'"'2026-01-01'"'"' GROUP BY campanha ORDER BY investimento DESC"
  }'
```

**11. Custo por compra ao longo do tempo**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT data_inicio, campanha, ROUND(custo_por_compra,2) AS custo_por_compra, ROUND(investimento,2) AS investimento FROM campanhas WHERE custo_por_compra > 0 ORDER BY data_inicio DESC LIMIT 30"
  }'
```

**12. Engajamento por campanha**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, SUM(engajamentos_post) AS engajamentos, SUM(comentarios) AS comentarios, SUM(compartilhamentos) AS compartilhamentos, SUM(reacoes) AS reacoes FROM campanhas GROUP BY campanha ORDER BY engajamentos DESC"
  }'
```

**13. CPM médio por mês**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT strftime('"'"'%Y-%m'"'"', data_inicio) AS mes, ROUND(AVG(cpm),2) AS cpm_medio, ROUND(SUM(impressoes),0) AS impressoes FROM campanhas GROUP BY mes ORDER BY mes"
  }'
```

**14. Top dias de pior performance (ROAS < 1)**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT data_inicio, campanha, ROUND(roas_compras,2) AS roas, ROUND(investimento,2) AS investimento, ROUND(valor_compras,2) AS receita FROM campanhas WHERE roas_compras > 0 AND roas_compras < 1 ORDER BY investimento DESC LIMIT 20"
  }'
```

**15. Comparativo Black November 2024 vs 2025**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, ROUND(SUM(investimento),2) AS investimento, ROUND(SUM(valor_compras),2) AS receita, ROUND(AVG(roas_compras),2) AS roas FROM campanhas WHERE campanha LIKE '"'"'%Black%'"'"' OR campanha LIKE '"'"'%black%'"'"' GROUP BY campanha ORDER BY campanha"
  }'
```

**16. Alcance acumulado por campanha**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, SUM(alcance) AS alcance_total, SUM(impressoes) AS impressoes_total, ROUND(CAST(SUM(impressoes) AS REAL)/NULLIF(SUM(alcance),0),2) AS freq_calculada FROM campanhas GROUP BY campanha ORDER BY alcance_total DESC"
  }'
```

**17. Dias com maior número de compras**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT data_inicio, SUM(compras) AS compras, ROUND(SUM(investimento),2) AS investimento, ROUND(SUM(valor_compras),2) AS receita FROM campanhas GROUP BY data_inicio ORDER BY compras DESC LIMIT 15"
  }'
```

**18. ROI mensal da Advantage_TodosProdutos_09/2025**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT strftime('"'"'%Y-%m'"'"', data_inicio) AS mes, ROUND(SUM(investimento),2) AS investimento, ROUND(SUM(valor_compras),2) AS receita, ROUND(AVG(roas_compras),2) AS roas FROM campanhas WHERE campanha = '"'"'Advantage_TodosProdutos_09/2025'"'"' GROUP BY mes ORDER BY mes"
  }'
```

**19. Análise de ThruPlays por campanha de vídeo**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, SUM(thruplays) AS thruplays, SUM(reproducoes_video) AS views_video, ROUND(AVG(custo_thruplay),2) AS custo_thruplay FROM campanhas WHERE thruplays > 0 GROUP BY campanha ORDER BY thruplays DESC"
  }'
```

**20. Correlação frequência × ROAS**
```bash
curl -X POST http://localhost:8001/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT campanha, ROUND(AVG(frequencia),2) AS frequencia, ROUND(AVG(roas_compras),2) AS roas, ROUND(AVG(cpm),2) AS cpm FROM campanhas WHERE frequencia > 0 AND roas_compras > 0 GROUP BY campanha ORDER BY frequencia DESC"
  }'
```

---

## 3. Endpoints REST Estruturados

### Referência rápida

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| `GET` | `/health` | Status da API e do banco |
| `GET` | `/campanhas` | Lista todas as campanhas |
| `GET` | `/resumo` | Resumo por campanha (filtros opcionais) |
| `GET` | `/top/roas` | Top campanhas por ROAS |
| `GET` | `/top/compras` | Top campanhas por compras |
| `GET` | `/campanha/{nome}` | Série temporal de uma campanha |
| `GET` | `/campanha/{nome}/funil` | Funil de conversão de uma campanha |
| `GET` | `/alertas/roas` | Campanhas com ROAS abaixo do threshold |
| `GET` | `/evolucao` | Evolução diária geral ou por campanha |
| `POST` | `/consulta` | Consulta em linguagem natural |
| `POST` | `/sql` | SQL livre (apenas SELECT) |
| `POST` | `/db/reload` | Recarrega CSV no banco |

---

### 📋 20 Exemplos — Endpoints REST

**1. Status da API**
```bash
curl http://localhost:8001/health
```

**2. Listar todas as campanhas**
```bash
curl http://localhost:8001/campanhas
```

**3. Resumo geral sem filtro**
```bash
curl http://localhost:8001/resumo
```

**4. Resumo de um período específico**
```bash
curl "http://localhost:8001/resumo?data_inicio=2026-01-01&data_fim=2026-06-23"
```

**5. Resumo do mês de maio/2026**
```bash
curl "http://localhost:8001/resumo?data_inicio=2026-05-01&data_fim=2026-05-31"
```

**6. Top 5 campanhas por ROAS**
```bash
curl "http://localhost:8001/top/roas?limit=5"
```

**7. Top 10 campanhas por ROAS nos últimos 90 dias**
```bash
curl "http://localhost:8001/top/roas?limit=10&data_inicio=2026-03-25&data_fim=2026-06-23"
```

**8. Top 5 campanhas por volume de compras**
```bash
curl "http://localhost:8001/top/compras?limit=5"
```

**9. Top compras em 2026**
```bash
curl "http://localhost:8001/top/compras?limit=10&data_inicio=2026-01-01&data_fim=2026-06-23"
```

**10. Série temporal diária da campanha ABO**
```bash
curl "http://localhost:8001/campanha/ABO_Testes_Todos_Produtos"
```

**11. Performance da campanha Advantage Hidratante em maio**
```bash
curl "http://localhost:8001/campanha/Advantage_HidratanteCorporal?data_inicio=2026-05-01&data_fim=2026-05-31"
```

**12. Série temporal da Black November 2025**
```bash
curl "http://localhost:8001/campanha/Black_November_2025_Advantage"
```

**13. Funil de conversão da campanha ABO (geral)**
```bash
curl "http://localhost:8001/campanha/ABO_Testes_Todos_Produtos/funil"
```

**14. Funil da Advantage Hidratante no último mês**
```bash
curl "http://localhost:8001/campanha/Advantage_HidratanteCorporal/funil?data_inicio=2026-05-24&data_fim=2026-06-23"
```

**15. Alertas de ROAS abaixo de 1.0 (padrão)**
```bash
curl "http://localhost:8001/alertas/roas"
```

**16. Alertas de ROAS abaixo de 2.0**
```bash
curl "http://localhost:8001/alertas/roas?threshold=2.0"
```

**17. Evolução diária geral (todas as campanhas)**
```bash
curl "http://localhost:8001/evolucao"
```

**18. Evolução diária de uma campanha específica**
```bash
curl "http://localhost:8001/evolucao?campanha=Advantage_TodosProdutos_09/2025"
```

**19. Evolução diária dos últimos 30 dias**
```bash
curl "http://localhost:8001/evolucao?data_inicio=2026-05-24&data_fim=2026-06-23"
```

**20. Recarregar banco após atualizar o CSV**
```bash
curl -X POST http://localhost:8001/db/reload
# {"status": "ok", "linhas_inseridas": 30905}
```

---

## Dicionário de Métricas

| Métrica | Coluna SQL | Descrição |
|---------|-----------|-----------|
| Investimento | `investimento` | Valor gasto em R$ |
| Receita | `valor_compras` | Valor de conversão das compras em R$ |
| ROAS | `roas_compras` | Receita / Investimento |
| Compras | `compras` | Número de compras atribuídas |
| Custo por compra | `custo_por_compra` | Investimento / Compras |
| Impressões | `impressoes` | Total de vezes que o anúncio foi exibido |
| Alcance | `alcance` | Pessoas únicas que viram o anúncio |
| Frequência | `frequencia` | Impressões / Alcance |
| CPM | `cpm` | Custo por 1.000 impressões |
| Cliques no link | `cliques_link` | Cliques que levam ao site |
| CPC | `cpc_link` | Custo por clique no link |
| CTR | `ctr_link` | Cliques / Impressões × 100 |
| Adições ao carrinho | `adicoes_carrinho` | Eventos de adicionar ao carrinho |
| Checkout iniciado | `checkout_iniciado` | Início do processo de compra |
| Views landing | `visualizacoes_landing` | Visualizações da página de destino |
| Views de conteúdo | `views_conteudo` | Visualizações de conteúdo do produto |
| ThruPlays | `thruplays` | Vídeos assistidos até o fim (ou 15s+) |
| Engajamentos | `engajamentos_post` | Total de interações com o post |

---

## Documentação Interativa

Com a API rodando, acesse:

- **Swagger UI:** http://localhost:8001/docs
- **ReDoc:** http://localhost:8001/redoc

---

*Agente de Performance Guará v2.0 — Guará Marketing*
