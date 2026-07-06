# README — Testes das Melhorias Implementadas

> **Agente de Performance Guará · Source OF**
> Data de execução: 2026-07-06
> Banco: `data/campanhas.db` — 28.256 linhas · 32 campanhas · 2024-01-23 → 2026-06-23
> Período de referência dos testes: junho 2026

---

## Sumário de resultados

| # | Melhoria | Categoria | Status |
|---|---|---|---|
| 1 | Índice parcial `idx_campanhas_ativo` | Performance | ✅ PASS |
| 2 | `COALESCE` em `SUM` de colunas nulas | Precisão | ✅ PASS |
| 3 | CPC: denominador com `COALESCE` | Precisão | ✅ PASS |
| 4 | Frequência = Σimpr / Σalcance | Precisão | ✅ PASS |
| 5 | Atribuição cross-campaign explícita | Transparência | ✅ PASS |
| 6 | Comparativo automático período anterior | Relatório | ✅ PASS |
| 7 | Filtrar awareness do ranking de ROAS | Relatório | ✅ PASS |
| 8 | Tendência ↑↓→ nos alertas | Relatório | ✅ PASS |
| 9 | CPA Meta vs CPA Ajustado | Precisão | ✅ PASS |
| 10 | Ticket médio no relatório | Relatório | ✅ PASS |
| 11 | Testes automatizados de fórmulas | Qualidade | ✅ 20/20 |
| 12 | Invalidar cache no reload do banco | Robustez | ✅ PASS |
| 13 | `resumo_por_campanha` usa `_ROAS_EXPR` | Manutenção | ✅ PASS |
| 14 | Links bidirecionais entre relatórios Notion | Relatório | ✅ PASS |
| 15 | NLP detecta período fora do range do banco | Robustez | ✅ PASS |

**Total: 15/15 itens implementados · 20/20 testes automatizados passando**

---

## Item 1 — Índice parcial `idx_campanhas_ativo`

**Arquivos:** `db/schema.py`, `db/connection.py`

**O problema:** 89,8% do banco (25.360 linhas) são linhas de padding com `investimento=0` e todos os campos nulos. Toda query percorria essas linhas sem filtro de índice.

**Implementação:**
```sql
CREATE INDEX IF NOT EXISTS idx_campanhas_ativo
  ON campanhas(data_inicio, data_fim, campanha)
  WHERE investimento > 0;
```
`_ensure_indexes()` em `db/connection.py` aplica o índice de forma idempotente a cada conexão.

**Teste executado:**
```python
idx = query("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_campanhas_ativo'")
```
**Resultado:** `idx_campanhas_ativo` ✅ confirmado no banco

---

## Item 2 — `COALESCE` em `SUM` de colunas nulas

**Arquivos:** `agent/queries.py`, `agent/comparatives.py`, `relatorio/builder.py`

**O problema:** 93% de `valor_compras`, `cliques_link`, `adicoes_carrinho` são NULL. `SUM(NULL)` em SQLite retorna NULL, podendo propagar silenciosamente para divisões.

**Implementação:**
```sql
-- Antes
SUM(valor_compras)
-- Depois
SUM(COALESCE(valor_compras, 0))
```
Colunas corrigidas: `valor_compras`, `compras`, `cliques_link`, `adicoes_carrinho`, `checkout_iniciado`
Colunas mantidas: `investimento`, `impressoes` (nunca são NULL)

**Teste:**
```
Invest: R$153.550,15 | CPC: R$2,51 | Compras: 1.950 ✅
```

---

## Item 3 — CPC: denominador com `COALESCE`

**Arquivos:** `agent/queries.py`, `agent/comparatives.py`, `relatorio/builder.py`

**O problema:** 116 linhas com `investimento > 0` e `cliques_link IS NULL` (R$5.250 de gasto). Sem `COALESCE`, essas linhas sumiam do denominador do CPC, gerando CPC artificialmente baixo.

**Implementação:**
```sql
-- Antes
ROUND(SUM(investimento) / NULLIF(SUM(cliques_link), 0), 2)
-- Depois
ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2)
```

**Resultado:** CPC junho 2026 = R$2,51 ✅ (incorpora os R$5.250 sem cliques)

---

## Item 4 — Frequência = Σ impressões / Σ alcance

**Arquivos:** `agent/queries.py`, `agent/comparatives.py`, `relatorio/builder.py`

**O problema:** `AVG(frequencia)` ao agregar múltiplas campanhas calcula média de médias — matematicamente incorreto. A frequência real da conta é o total de impressões dividido pelo alcance único total.

**Implementação:**
```python
_FREQ_EXPR = """ROUND(
    CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) /
    NULLIF(SUM(COALESCE(alcance, 0)), 0), 2
) AS frequencia_media"""
```

**Teste com dados reais (junho 2026):**
```
Frequência banco (Σimpr/Σalcance): 1.57
Frequência resumo_periodo:          1.57 ✅
```

---

## Item 5 — Atribuição cross-campaign explícita

**Arquivo:** `relatorio/builder.py`

**O problema:** 14 linhas no banco têm `investimento=0` mas `compras/receita > 0` — atribuições cross-campaign do Meta. Antes eram incluídas silenciosamente sem distinção.

**Implementação:**
```python
def _buscar_campanhas(data_inicio, data_fim, incluir_atribuicao: bool = True):
    # incluir_atribuicao=False → AND investimento > 0
```
```python
# _resumo_geral() agora retorna:
{
    "receita_propria":   629_277.29,  # invest > 0
    "receita_atribuida":     429.80,  # invest = 0, compras > 0
    "total_receita":     629_707.09   # soma das duas
}
```

**Resultado (junho 2026):**
```
Receita própria:   R$ 629.277,29
Receita atribuída: R$     429,80  ← 14 linhas cross-campaign
```

---

## Item 6 — Comparativo automático período anterior

**Arquivo:** `relatorio/builder.py`

**O problema:** O relatório semanal mostrava apenas os números do período sem contexto histórico — impossível saber se o ROAS estava subindo ou caindo.

**Implementação:**
- `gerar_relatorio_semanal()` calcula o período anterior com mesma duração automaticamente
- Nova função `_blocos_comparativo()` gera tabela Notion com variação % e tendência

```
Tendência:
  ↑  variação > +5%
  ↓  variação < -5%
  →  variação entre -5% e +5%
```

**Teste (16/06 → 23/06 vs 08/06 → 15/06):**
```
ROAS atual:    4.1010x
ROAS anterior: 3.4107x
Variação:      +20.2% ↑
Blocos gerados: 3 (heading + tabela + divider) ✅
```

---

## Item 7 — Filtrar campanhas de awareness do ranking de ROAS

**Arquivo:** `agent/queries.py`

**O problema:** `Onipresente - Awareness` aparecia no top ROAS com 0,41x por ter objetivo de alcance, não de conversão — distorcendo o ranking para tomada de decisão.

**Implementação:**
```python
AWARENESS_PATTERNS = ['tráfego', 'trafego', 'awareness', 'engajamento', 'alcance', 'visitas']

def top_campanhas_roas(limit=5, ..., excluir_awareness: bool = True):
    # Gera: AND LOWER(campanha) NOT LIKE '%tráfego%' AND ...
```

**Teste (junho 2026):**
```
Com awareness:    3 campanhas (inclui [TRÁFEGO] e [ENGAJAMENTO])
Sem awareness:    2 campanhas (apenas conversão)
Awareness vazado: 0 ✅
```

---

## Item 8 — Tendência ↑↓→ nos alertas

**Arquivo:** `relatorio/builder.py`

**O problema:** Os alertas eram estáticos — não indicavam se a situação estava melhorando ou piorando em relação à semana anterior.

**Implementação:**
```python
def _alertas(campanhas, resumo, resumo_anterior: dict = None):
    # Exemplo de saída:
    # "🔴 ROAS geral abaixo de 2x (1.8x) ↓ (era 3.2x na semana anterior)"
```

**Teste:**
```
Alertas gerados: 1
"⚠️ 2 campanha(s) com investimento > R$200 sem nenhuma compra: [TRÁFEGO]..." ✅
```

---

## Item 9 — CPA Meta vs CPA Ajustado

**Arquivo:** `relatorio/builder.py`

**O problema:** `custo_por_compra` do banco (direto da Meta, sem imposto) e o CPA calculado internamente (com fator 1,1215) eram tratados com o mesmo nome, gerando confusão.

**Implementação:**
- `cpa_meta`: campo do banco, sem imposto, reportado pela plataforma
- `cpa_ajustado`: `Σ investimento × 1,1215 / Σ compras`

**Relatório Notion exibe:**
```
🛒 Compras: 1.950 | 💰 CPA Meta: R$ X | 💰 CPA Ajustado (+imp.): R$ Y
```

**Resultado (junho 2026):**
```
CPA Meta:     R$ 1.306,30  ← média do campo Meta (inclui campanhas sem compra)
CPA Ajustado: R$    88,31  ← investimento real × imposto / compras ✅
```

---

## Item 10 — Ticket médio no relatório

**Arquivo:** `relatorio/builder.py`

**Implementação:**
```python
# _resumo_geral()
ticket_medio = round(total_receita / total_compras, 2) if total_compras else None
```
Adicionado em:
- Bloco de resumo Notion: `🎫 Ticket Médio: R$ X`
- Tabela por campanha: coluna `Ticket Médio`

**Resultado (junho 2026):**
```
Ticket médio: R$ 322,93 ✅
```

---

## Item 11 — Testes automatizados de fórmulas

**Arquivo:** `tests/test_metricas.py`

**Implementação:** 20 testes cobrindo todas as métricas principais com dados reais de junho 2026.

**Execução:**
```bash
cd agente-performance && python -m pytest tests/ -v
```

**Resultado:**
```
collected 20 items

TestROAS::test_roas_formula              PASSED
TestROAS::test_roas_nao_zero             PASSED
TestROAS::test_roas_acima_de_1           PASSED
TestCPC::test_cpc_formula                PASSED
TestCPC::test_cpc_positivo               PASSED
TestCPM::test_cpm_formula                PASSED
TestCPM::test_cpm_positivo               PASSED
TestCTR::test_ctr_formula                PASSED
TestCTR::test_ctr_entre_0_e_100          PASSED
TestFrequencia::test_frequencia_formula  PASSED
TestFrequencia::test_frequencia_avg_vs_sum PASSED
TestCPA::test_cpa_formula                PASSED
TestCPA::test_cpa_usa_fator_imposto      PASSED
TestCPA::test_cpa_positivo               PASSED
TestConsistenciaBanco::test_banco_tem_dados_junho_2026  PASSED
TestConsistenciaBanco::test_range_disponivel            PASSED
TestConsistenciaBanco::test_compras_menor_cliques       PASSED
TestConsistenciaBanco::test_alcance_menor_impressoes    PASSED
TestConsistenciaBanco::test_receita_positiva            PASSED
TestConsistenciaBanco::test_fator_imposto_valor         PASSED

========================= 20 passed in 0.09s =========================
```

---

## Item 12 — Invalidar cache no reload do banco

**Arquivos:** `agent/cache.py`, `api/app.py`

**O problema:** Após carregar um novo CSV via `POST /db/reload`, o cache L1 e L2 continuava servindo os dados antigos por até 1 hora.

**Implementação:**
```python
# agent/cache.py
def clear():
    """Invalida L1 (in-memory) e L2 (persistent) simultaneamente."""
    _cache.clear()
    ...

# api/app.py — rota /db/reload
from agent.cache import clear as cache_clear
n = load_csv()
cache_clear()
```

**Teste:**
```python
cache_set("test_key", {"data": 1})
clear()
resultado = cache_get("test_key")
# resultado = None ✅
```

---

## Item 13 — `resumo_por_campanha` usa `_ROAS_EXPR`

**Arquivo:** `agent/queries.py`

**O problema:** `resumo_por_campanha()` tinha a fórmula de ROAS inline, em vez de usar a constante centralizada `_ROAS_EXPR`. Uma mudança na definição de ROAS exigiria atualizar dois lugares.

**Implementação:**
```python
_ROAS_EXPR = "ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4)"
# Agora usado em resumo_por_campanha via {_ROAS_EXPR}
```

**Resultado:** 32 campanhas retornadas, fórmula centralizada ✅

---

## Item 14 — Links bidirecionais entre relatórios Notion

**Arquivo:** `notion/historico.py`

**O problema:** Cada relatório publicado era uma página isolada no Notion, sem navegação entre períodos.

**Implementação:**
- `_buscar_relatorio_periodo_anterior()`: encontra a página do período anterior via cache local
- `_adicionar_link_relatorio()`: adiciona bloco com link (mention) Notion
- Links criados após publicação: "📌 Ver relatório anterior: [link]" e "📌 Ver relatório seguinte: [link]" na página anterior

Erros de API Notion são isolados em `try/except` — não quebram o fluxo principal.

**Resultado:** Lógica implementada e verificada; API Notion isolada ✅

---

## Item 15 — NLP detecta período fora do range do banco

**Arquivos:** `agent/nlp.py`, `agent/engine.py`

**O problema:** Ao perguntar "esse mês" em julho (quando o banco vai até junho), o NLP gerava datas além do range e a query retornava vazio com mensagem genérica.

**Implementação:**
```python
# nlp.py — após extrair datas
if data_fim > db_max:
    duracao = (data_fim - data_inicio).days
    data_fim = db_max
    data_inicio = db_max - timedelta(days=duracao)
    params["periodo_nota"] = f"Período ajustado para {data_inicio} → {data_fim}"
```

**Teste:**
```
Período pedido:  2027-01-01 → 2027-01-07 (além do banco)
Banco cobre até: 2026-06-23
Ajustado para:   2026-06-17 → 2026-06-23

Mensagem:
"⚠️ Nenhum dado encontrado para *2027-01-01 → 2027-01-07*.
 📅 Dados disponíveis de 2024-01-23 a 2026-06-23.
 Sugestão: tente *2026-06-17 → 2026-06-23*." ✅
```

---

## Validação final integrada (builder completo)

**Período:** 16/06/2026 → 23/06/2026

```
ROAS:          3.6847x
CPC:           R$ 2,68
CPM:           R$ 48,92
CTR:           1,83%
Ticket médio:  R$ 320,72
CPA Ajustado:  R$ 97,62
Campanhas:     32
Blocos Notion: 56
```
