# Testes de Implementação — Agente de Performance Guará
**Data:** 2026-08-18 | **Commit base:** `42cc78c`

Este documento registra os testes executados para cada funcionalidade implementada nas sessões de 2026-08-18, com os resultados reais capturados em ambiente de produção.

---

## Módulos testados

| Módulo | Arquivo | Status |
|--------|---------|--------|
| BaseAnalysisAgent | `agent/base_agent.py` | ✅ |
| SQL Generator (direto + planner) | `agent/sql_generator.py` | ✅ |
| Claude Client (resultado estruturado + grounding) | `agent/claude_client.py` | ✅ |
| Engine (intents fixos + fallback SQL) | `agent/engine.py` | ✅ |
| Serviço HTTP | `http://localhost:8001` | ✅ |

---

## Bloco 1 — Heurística de Complexidade (`_is_complex_query`)

Identifica automaticamente perguntas que precisam do Query Planner (2 chamadas LLM) vs. modo direto (1 chamada).

**Gatilhos de complexidade:** `compare`, `vs`, `versus`, `semana passada`, `mês passado`, `em relação a`, `semana a semana`, `período anterior`

```
Resultado: 8/8 corretos
```

| Pergunta | Esperado | Resultado |
|----------|----------|-----------|
| compare ROAS semana passada vs essa semana | COMPLEXA | ✅ COMPLEXA |
| top 10 campanhas por ROAS | SIMPLES | ✅ SIMPLES |
| campanhas com CTR acima de 1.5 | SIMPLES | ✅ SIMPLES |
| compare meta_ads vs google_ads em relação ao mês passado | COMPLEXA | ✅ COMPLEXA |
| semana a semana por plataforma | COMPLEXA | ✅ COMPLEXA |
| investimento total da semana | SIMPLES | ✅ SIMPLES |
| quais campanhas tiveram mais compras ontem | SIMPLES | ✅ SIMPLES |
| versus período anterior | COMPLEXA | ✅ COMPLEXA |

---

## Bloco 2 — Guardrails de Validação SQL (`_validate_sql`)

4 proteções aplicadas a todo SQL gerado pelo LLM antes da execução.

| SQL de entrada | Resultado | Comportamento |
|----------------|-----------|---------------|
| `SELECT ... WHERE cliente_key='source_of' LIMIT 10` | ✅ VÁLIDO | Passa sem alteração |
| `DROP TABLE campanhas_unificada` | 🚫 BLOQUEADO | `"SQL gerado não contém SELECT."` |
| `UPDATE campanhas_unificada SET investimento=0` | 🚫 BLOQUEADO | `"SQL gerado não contém SELECT."` |
| `SELECT campanha FROM campanhas_unificada` (sem LIMIT) | ✅ VÁLIDO | LIMIT 50 injetado automaticamente |
| `SELECT campanha FROM ... LIMIT 5` (sem cliente_key) | ✅ VÁLIDO | `WHERE cliente_key='source_of'` injetado |

---

## Bloco 3 — Geração SQL Modo Direto (1 chamada LLM)

**Pergunta:** `top 5 campanhas por ROAS na última semana`
**Latência:** 1513ms

```sql
SELECT
  campanha,
  plataforma,
  ROUND(SUM(valor_compras)/NULLIF(SUM(investimento),0), 2) AS roas,
  ROUND(SUM(investimento), 2) AS investimento_total,
  ROUND(SUM(valor_compras), 2) AS valor_compras_total,
  SUM(compras) AS compras_total
FROM campanhas_unificada
WHERE cliente_key = 'source_of'
  AND data_inicio >= '2026-08-11'
  AND data_fim <= '2026-08-17'
GROUP BY campanha, plataforma
ORDER BY roas DESC
LIMIT 5
```

| Verificação | Resultado |
|-------------|-----------|
| Apenas SELECT | ✅ |
| LIMIT presente | ✅ |
| cliente_key presente | ✅ |
| ROAS calculado corretamente (SUM/SUM, não AVG) | ✅ |

---

## Bloco 4 — Query Planner (LLM → JSON estruturado)

**Pergunta:** `compare ROAS e investimento semana passada vs essa semana por plataforma`
**Latência:** 1233ms

O Planner infere automaticamente o período de comparação (semana anterior) a partir do período de referência fornecido.

```json
{
  "tipo": "comparativo_temporal",
  "metricas": ["roas", "investimento"],
  "granularidade": "plataforma",
  "periodo_a": {"inicio": "2026-08-11", "fim": "2026-08-17"},
  "periodo_b": {"inicio": "2026-08-04", "fim": "2026-08-10"},
  "filtros": [],
  "ordenacao": "plataforma ASC",
  "limite": 50,
  "usar_having": false
}
```

| Verificação | Resultado |
|-------------|-----------|
| Tipo correto (`comparativo_temporal`) | ✅ |
| Métricas inferidas (`roas`, `investimento`) | ✅ |
| Granularidade (`plataforma`) | ✅ |
| Período A correto (semana atual) | ✅ |
| Período B inferido automaticamente (semana anterior) | ✅ |

---

## Bloco 5 — Pipeline Completo SQL Dinâmico (`executar_sql_dinamico`)

### 5a — Modo Direto

**Pergunta:** `campanhas com CTR acima de 1 na última semana`

```
modo=direto | linhas=43 | erro=None | 1609ms
Campos: campanha, plataforma, data_inicio, data_fim, impressoes,
        cliques_link, ctr, investimento, valor_compras, roas
```

### 5b — Modo Planner (comparativo temporal)

**Pergunta:** `compare investimento e ROAS semana passada vs essa semana por plataforma`

```
modo=planner | linhas=2 | erro=None | 3964ms
```

**Dados retornados (reais do banco):**

| plataforma | invest_atual | invest_anterior | delta_abs | delta_pct | roas_atual | roas_anterior | delta_roas |
|------------|-------------|-----------------|-----------|-----------|------------|---------------|------------|
| google_ads | R$ 1.879,50 | R$ 3.488,78 | -R$ 1.609,28 | -46,13% | 9,07x | 9,21x | -0,14 |
| meta_ads | R$ 49.396,86 | R$ 54.656,72 | -R$ 5.259,86 | -9,62% | 4,68x | 4,59x | +0,09 |

---

## Bloco 6 — Resultado Estruturado (`_formatar_dados`)

Substitui serialização `k=v` por JSON compacto + sumário estatístico.

### 6a — `list[dict]` (3 campanhas)

```
Total de linhas: 3
Sumário: investimento: total=17332.50 | valor_compras: total=93279.00 | roas_compras: média=5.70 | compras: total=303.00

  1. {"campanha": "Advantage+", "investimento": 8432.5, "valor_compras": 43850.0, "roas_compras": 5.2, "compras": 143.0}
  2. {"campanha": "ABO Catálogo", "investimento": 6100.0, "valor_compras": 29829.0, "roas_compras": 4.89, "compras": 98.0}
  3. {"campanha": "Retargeting", "investimento": 2800.0, "valor_compras": 19600.0, "roas_compras": 7.0, "compras": 62.0}
```

### 6b — `dict` (resumo geral)

```json
{
  "investimento": 49396.86,
  "valor_compras": 231175.72,
  "compras": 847.0,
  "roas_compras": 4.68,
  "ctr_link": 0.86
}
```

### 6c — Lista grande (35 itens) — truncamento

```
Total de linhas: 35 (exibindo primeiras 30)
Sumário: roas_compras: média=2.70 | investimento: total=94500.00
  ... e mais 5 registros
```

| Verificação | Resultado |
|-------------|-----------|
| Sumário com totais para volume (investimento, compras) | ✅ |
| Sumário com médias para taxas (roas, ctr, cpm) | ✅ |
| Truncamento em 30 linhas com contagem do total | ✅ |
| Valores None omitidos do JSON | ✅ |
| dict → json.dumps indentado | ✅ |

---

## Bloco 7 — Grounded Generation

### Camada 1 — Citation Forcing (sempre ativa)

5 regras injetadas no `user_message` antes de cada chamada ao LLM:

```
[REGRAS DE GROUNDING — OBRIGATÓRIAS]
1. Cite APENAS valores que aparecem literalmente nos dados acima.
2. NUNCA estime, arredonde de forma inventada ou complete com conhecimento geral.
3. Se um dado não está disponível nos dados fornecidos, diga explicitamente: "dado não disponível".
4. Cada número na resposta deve ser rastreável a um campo nos dados acima.
5. Variações percentuais: calcule a partir dos valores fornecidos — não invente tendências.
```

| Verificação | Resultado |
|-------------|-----------|
| Regras definidas | ✅ |
| Total de regras | 5 |
| Custo adicional de tokens | zero (inserção de texto, sem chamada extra) |

### Camada 2 — Structured Output (opt-in via `meta["structured_output"] = True`)

**Parse de JSON estruturado com rastreabilidade:**

```json
{
  "headline": "ROAS caiu 8% vs semana anterior",
  "periodo": "11 ago → 17 ago",
  "metricas_citadas": [
    {"nome": "roas_atual",    "valor": 4.68,     "fonte": "dados[0].roas_atual"},
    {"nome": "roas_anterior", "valor": 5.09,     "fonte": "dados[0].roas_anterior"},
    {"nome": "investimento",  "valor": 49396.86, "fonte": "dados[0].investimento"}
  ],
  "analise": "📅 _Analisando: 11 ago → 17 ago · Source OF · Meta Ads_\n\n✅ ROAS estável...",
  "alertas": ["ROAS abaixo de 5", "Google Ads com queda de 46% no investimento"],
  "recomendacoes": ["Revisar orçamento Google Ads", "Testar novos criativos Advantage+"],
  "dado_nao_disponivel": []
}
```

| Verificação | Resultado |
|-------------|-----------|
| Campos extraídos | ✅ 7/7 (`headline`, `periodo`, `metricas_citadas`, `analise`, `alertas`, `recomendacoes`, `dado_nao_disponivel`) |
| Métricas com fonte rastreável | ✅ 3 métricas, cada uma com `dados[N].campo` |
| Fallback para texto bruto se JSON inválido | ✅ retorna `None` → usa `raw_text` |

---

## Bloco 8 — BaseAnalysisAgent

| Teste | Resultado |
|-------|-----------|
| fuzzy match `"Advantage+"` → `"Advantage+ Shopping"` | ✅ |
| campanha inexistente → `match=None`, erro com sugestões | ✅ |
| `None` passado → sem erro, sem match | ✅ |
| `empty_suggestion` busca range real do banco | ✅ `2024-01-23 → 2026-08-17` |

**Saída de `empty_suggestion` com dados reais:**
```
⚠️ Nenhum dado encontrado para o período solicitado.
_Dados disponíveis: 2024-01-23 → 2026-08-17_
Tente: `resumo de 2024-01-23 a 2026-08-17`
```

---

## Bloco 9 — engine.consultar (intents fixos)

| Pergunta | Intent detectado | Dados | SQL dinâmico | Latência |
|----------|------------------|-------|--------------|----------|
| top 5 campanhas por ROAS essa semana | `top_roas` | ✅ | ❌ | 1358ms |
| resumo geral da semana passada | `resumo_geral` | ✅ | ❌ | 1105ms |

Intents conhecidos continuam no caminho rápido sem passar pelo fallback SQL.

---

## Bloco 10 — SQL Dinâmico ad-hoc

**Pergunta ad-hoc:** `campanhas que tiveram checkout_iniciado acima de 50 e compras abaixo de 20`

```
modo=direto | latência=1435ms
```

SQL gerado pelo LLM para filtro multi-condição complexo:
```sql
SELECT
  data_inicio, data_fim, campanha, cliente_nome, plataforma,
  SUM(checkout_iniciado) AS total_checkout,
  SUM(compras) AS total_compras,
  ...
FROM campanhas_unificada
WHERE cliente_key = 'source_of'
  AND data_inicio >= '2026-08-11'
GROUP BY ...
HAVING SUM(checkout_iniciado) > 50 AND SUM(compras) < 20
LIMIT 50
```

---

## Bloco 11 — Serviço HTTP

```
GET http://localhost:8001/health
Status: 200 OK
{"status": "ok", "db_exists": true, "db_size_kb": 25412.0}
```

---

## Resumo de Latências

| Operação | Latência típica |
|----------|-----------------|
| NLP (intent) | ~800ms |
| engine.consultar (intent fixo) | 1100–1400ms |
| SQL modo direto (1 LLM) | 1400–1600ms |
| Query Planner (2 LLM) | 3500–4000ms |
| _formatar_dados (sem LLM) | < 1ms |
| _parse_structured_output (sem LLM) | < 1ms |

---

## Arquitetura Atual (pós-implementação)

```
Pergunta
  │
  ▼
NLP (interpretar) ─────────────────────────────────────────────────
  │                                                                 │
  │ intent conhecido                                   desconhecida │
  ▼                                                                 ▼
_executar_intent()                              _is_complex_query()?
  │                                                 │           │
  ▼                                              sim           não
queries.py (SQL fixo)                             │             │
  │                                               ▼             ▼
  ▼                                        Planner (LLM1)  Generator (LLM1)
dados                                       + Generator     SQL direto
  │                                         (LLM2)
  └──────────────────────────────────────────────┘
                      │
                      ▼
             _formatar_dados()  ← JSON compacto + sumário
                      │
                      ▼
             gerar_resposta()
               │
               ├── [GROUNDING CAMADA 1] citation forcing (5 regras)
               ├── [GROUNDING CAMADA 2] structured output (opt-in)
               │
               ▼
             Claude Sonnet → resposta Slack
```

---

## Commits desta sessão

| Hash | Descrição |
|------|-----------|
| `25cce44` | feat: SQL dinâmico (Text-to-SQL) como fallback para perguntas ad-hoc |
| `1031b77` | feat: Query Planner — 2-stage LLM para perguntas complexas |
| `148330b` | docs: FASE2.md — spec de arquitetura multi-agentes |
| `1b27053` | refactor: resultado estruturado — _formatar_dados usa JSON compacto + sumário |
| `42cc78c` | feat: Grounded Generation — Camada 1 (citation forcing) + Camada 2 (structured output) |
