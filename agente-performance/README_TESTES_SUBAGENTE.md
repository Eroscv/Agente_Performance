# README — Testes do Subagente de Performance por Completo

> **Agente de Performance Guará · Source OF**
> Data: 2026-07-06
> Banco: `data/campanhas.db` — 28.256 linhas · 32 campanhas · 2024-01-23 → 2026-06-23

---

## Arquitetura do subagente

```
Pergunta em linguagem natural (Slack / API)
        ↓
  agent/nlp.py          → Claude interpreta intents + datas + campanha
        ↓
  agent/engine.py       → valida campanha, executa queries, verifica cache
        ↓
  agent/queries.py      → SQL no SQLite (métricas com fórmulas corretas)
  agent/comparatives.py → comparações período a período / campanha a campanha
        ↓
  agent/claude_client.py → formata resposta em linguagem natural
        ↓
  slack/handlers.py      → posta no Slack ou exporta (Excel / PDF / Notion)
  relatorio/builder.py   → gera páginas estruturadas no Notion
```

---

## 1. Testes automatizados de fórmulas

```bash
python -m pytest tests/ -v
```

```
collected 20 items

TestROAS::test_roas_formula                              PASSED
TestROAS::test_roas_nao_zero                             PASSED
TestROAS::test_roas_acima_de_1                           PASSED
TestCPC::test_cpc_formula                                PASSED
TestCPC::test_cpc_positivo                               PASSED
TestCPM::test_cpm_formula                                PASSED
TestCPM::test_cpm_positivo                               PASSED
TestCTR::test_ctr_formula                                PASSED
TestCTR::test_ctr_entre_0_e_100                          PASSED
TestFrequencia::test_frequencia_formula                  PASSED
TestFrequencia::test_frequencia_avg_vs_sum               PASSED
TestCPA::test_cpa_formula                                PASSED
TestCPA::test_cpa_usa_fator_imposto                      PASSED
TestCPA::test_cpa_positivo                               PASSED
TestConsistenciaBanco::test_banco_tem_dados_junho_2026   PASSED
TestConsistenciaBanco::test_range_disponivel             PASSED
TestConsistenciaBanco::test_compras_menor_cliques        PASSED
TestConsistenciaBanco::test_alcance_menor_impressoes     PASSED
TestConsistenciaBanco::test_receita_positiva             PASSED
TestConsistenciaBanco::test_fator_imposto_valor          PASSED

========================= 20 passed in 0.09s =========================
```

---

## 2. Precisão das métricas — Junho 2026

Fórmulas verificadas contra a fonte direta do banco (SQL puro sem Python):

| Métrica | Fórmula | Resultado |
|---|---|---|
| ROAS | Σ valor_compras / Σ investimento | **4.1010x** |
| CPC | Σ investimento / Σ cliques_link | **R$ 2,51** |
| CPM | Σ investimento / Σ impressoes × 1000 | **R$ 45,78** |
| CTR | Σ cliques_link / Σ impressoes × 100 | **1,83%** |
| CPA Ajustado | Σ investimento × 1,1215 / Σ compras | **R$ 88,31** |
| Frequência | Σ impressoes / Σ alcance | **1,57** |
| Ticket médio | Σ receita / Σ compras | **R$ 322,93** |
| Invest total | Σ investimento | **R$ 153.550,15** |
| Receita total | Σ valor_compras | **R$ 629.707,09** |
| Compras | Σ compras | **1.950** |

Consistência entre camadas:

| Fonte | ROAS | CPC | CPM | CTR | Compras |
|---|---|---|---|---|---|
| SQL direto (referência) | 4.1010 | 2.51 | 45.78 | 1.83 | 1.950 |
| `resumo_periodo()` | 4.1010 ✅ | 2.51 ✅ | 45.78 ✅ | 1.83 ✅ | 1.950 ✅ |
| `builder._resumo_geral()` | 4.1010 ✅ | 2.51 ✅ | 45.78 ✅ | 1.83 ✅ | 1.950 ✅ |

---

## 3. Testes de NLP — Interpretação de perguntas

10 perguntas em linguagem natural, cada uma verificando intent detectado, dados retornados e ausência de erro:

| Pergunta | Intent | Dados | Status |
|---|---|---|---|
| "como foi junho de 2026?" | `periodo` | 1 dict | ✅ |
| "top 3 campanhas por ROAS em junho" | `top_roas` | 3 linhas | ✅ |
| "quais campanhas geraram mais compras em junho?" | `ranking_metrica` | 5 linhas | ✅ |
| "mostre o funil de conversão geral em junho" | `funil` | 1 dict | ✅ |
| "quais campanhas estão com ROAS ruim?" | `alertas_roas` | 1 linha | ✅ |
| "compare maio de 2026 com junho de 2026" | `comparar_periodos` | 1 dict | ✅ |
| "evolução semana a semana" | `comparar_wow` | 6 linhas | ✅ |
| "ranking de campanhas por CPC em junho" | `ranking_metrica` | 6 linhas | ✅ |
| "liste todas as campanhas disponíveis" | `listar_campanhas` | 32 linhas | ✅ |
| "performance da ABO em junho" | `performance_campanha` | 30 linhas | ✅ |

---

## 4. Testes de queries diretas

### 4.1 `top_campanhas_roas` — com filtro de awareness

```python
top = top_campanhas_roas(5, "2026-06-01", "2026-06-30", excluir_awareness=True)
```
```
ABO_Testes_Todos_Produtos           — ROAS 4.2994x
Advantage_TodosProdutos_05/2026     — ROAS 4.1993x
Campanhas awareness excluídas: ✅ (0 vazamentos)
```

### 4.2 `alertas_roas_baixo`

```
1 campanha com ROAS abaixo do threshold:
  Onipresente - Awareness — ROAS 0.4885x ⚠️
```

### 4.3 `metricas_funil` — Junho 2026

```
Impressões:   3.353.738
      ↓
Cliques:         61.259  (CTR 1,83%)
      ↓
Carrinho:         4.681  (7,6% dos cliques)
      ↓
Checkout:         3.880  (82,9% do carrinho)
      ↓
Compras:          1.950  (50,3% do checkout)

Investimento: R$ 153.550,15
Receita:      R$ 629.707,09
```

### 4.4 `comparar_mes_a_mes` — últimos meses

```
Mai/26: ROAS 3.4324x | Invest R$ 302.451 | Compras 3.530
Jun/26: ROAS 4.1010x | Invest R$ 153.550 | Compras 1.950
```

### 4.5 `listar_campanhas` — top 3 por investimento

```
1. ABO_Testes_Todos_Produtos              — R$ 1.961.639
2. Advantage_TodosProdutos_09/2025        — R$   309.324
3. Primeira_Geral_Advantage_Campanha_CC   — R$   184.691
Total: 32 campanhas cadastradas
```

---

## 5. Testes do builder de relatório

### 5.1 Relatório semanal (16/06 → 23/06)

```
Campanhas:        32
Blocos Notion:    56
ROAS:             3.6847x
Investimento:     R$ 63.105,16
Receita:          R$ 232.523,06
Compras:          725
CPA Ajustado:     R$ 97,62
Ticket Médio:     R$ 320,72
CPM:              R$ 48,92
CPC:              R$ 2,68
CTR:              1,83%
Frequência:       1,58
```

### 5.2 Seções do relatório Notion (verificadas nos blocos)

```
✅ 📊 Resumo Geral da Conta
✅ 📈 Comparativo com Período Anterior (08/06/2026 → 15/06/2026)
✅ 📋 Desempenho por Campanha
✅ 🏆 Top Campanhas por ROAS
✅ 💰 Alocação de Budget
✅ 🚨 Alertas Automáticos
```

### 5.3 Comparativo automático período anterior

```
Período atual:    16/06 → 23/06  | ROAS 3.6847x
Período anterior: 08/06 → 15/06  | ROAS 4.1010x
Variação ROAS:    -10.1% ↓
```

### 5.4 Relatório mensal (Maio 2026)

```
Campanhas:     32
Blocos Notion: 58
```

---

## 6. Testes de robustez

### 6.1 Período fora do range do banco

```python
consultar("resumo de julho de 2027")
```
```
Banco cobre: 2024-01-23 → 2026-06-23
Período pedido: 2027-07-01 → 2027-07-31 (além do range)
Ação: ajuste automático para 2026-05-24 → 2026-06-23
Mensagem:
"⚠️ Nenhum dado encontrado para *2027-07-01 → 2027-07-31*.
 📅 Dados disponíveis de 2024-01-23 a 2026-06-23.
 Sugestão: tente *2026-05-24 → 2026-06-23*." ✅
```

### 6.2 Cache invalidado no reload

```python
cache_set("test_key", {"data": 1})
clear()
cache_get("test_key")  # → None ✅
```

### 6.3 Atribuição cross-campaign

```
14 linhas com investimento=0 e compras>0 (atribuição Meta)
receita_propria:   R$ 629.277,29  (campanhas com spend)
receita_atribuida: R$     429,80  (cross-campaign)
total_receita:     R$ 629.707,09  ✅
```

### 6.4 Campanha inexistente

```python
consultar("performance da campanha XYZINEXISTENTE")
```
```
Resposta: "⚠️ Não encontrei nenhuma campanha com o nome XYZINEXISTENTE.
           Campanhas disponíveis (top 8): ABO_Testes_Todos_Produtos, ..."
Dados retornados: [] — sem fallback silencioso ✅
```

---

## 7. Índice de performance do banco

```
Banco: 28.256 linhas (89,8% são linhas de padding invest=0)
Índice parcial idx_campanhas_ativo: ✅ criado
  ON campanhas(data_inicio, data_fim, campanha) WHERE investimento > 0
```

---

## 8. Estado dos módulos

| Módulo | Status |
|---|---|
| `db/connection.py` + `db/schema.py` | ✅ Índice parcial ativo |
| `db/loader.py` | ✅ Carga idempotente |
| `db/validator.py` | ✅ Validação pré-relatório |
| `agent/queries.py` | ✅ Todas fórmulas com SUM/SUM |
| `agent/comparatives.py` | ✅ COALESCE + fórmulas corretas |
| `agent/nlp.py` | ✅ Detecta período fora do range |
| `agent/engine.py` | ✅ Sugere período alternativo |
| `agent/cache.py` | ✅ `clear()` implementado |
| `relatorio/builder.py` | ✅ Comparativo + ticket + CPA duplo |
| `notion/historico.py` | ✅ Links bidirecionais |
| `api/app.py` | ✅ Cache invalidado no reload |
| `tests/test_metricas.py` | ✅ 20/20 testes passando |
| `slack/handlers.py` | ✅ Awareness filtrado |

---

## 9. Resumo de cobertura

| Camada | Testes | Status |
|---|---|---|
| Fórmulas de negócio | 20 testes automatizados | ✅ 20/20 |
| NLP — interpretação | 10 perguntas variadas | ✅ 10/10 |
| Queries SQL | 8 funções testadas | ✅ 8/8 |
| Builder de relatório | Semanal + mensal | ✅ |
| Robustez | 4 cenários de borda | ✅ 4/4 |
| Cache | Set + Clear + Reload | ✅ |
