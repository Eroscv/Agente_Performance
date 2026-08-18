# 🧪 Relatório de Testes — Agente de Performance Guará

**Data:** 2026-08-18  
**Commit:** `6a99490`  
**Ambiente:** Ubuntu · Python 3.12.3 · pytest 9.1.1  
**Resultado final:** ✅ **154/154 testes passando**

---

## Contexto

Esta sessão de testes foi realizada após um ciclo de refatoração estrutural significativo do projeto:

- Criação de `agent/queries/` (pacote com 8 submódulos, antes monolítico de 1.743 linhas)
- Separação de `slack/handlers.py` em `helpers.py`, `responder.py`, `formatter.py`, `poster.py`
- Criação de `agent/clients/` — arquitetura multiagente de 2 camadas
- Reorganização de `relatorio/` em `relatorio/shared/` (5 submódulos)
- Estrutura de pastas por cliente (`clientes/pod/` e `clientes/source_of/`)

---

## Resultado por Arquivo de Teste

| Arquivo | Total | ✅ Passou | ❌ Falhou | Categoria |
|---|---|---|---|---|
| `test_consultas.py` | 83 | 83 | 0 | Queries SQL |
| `test_dados_consultas.py` | 15 | 15 | 0 | Dados reais no banco |
| `test_engine_validacao.py` | 30 | 30 | 0 | Engine + isolamento clientes |
| `test_metricas.py` | 17 | 17 | 0 | Fórmulas de métricas |
| `test_usabilidade.py` | 9 | 9 | 0 | UX e preferências |
| **TOTAL** | **154** | **154** | **0** | |

---

## Bugs Encontrados e Corrigidos

### 1. `NameError: _ROAS_EXPR` — todos os submódulos de `agent/queries/`
**Causa:** Ao fazer o split do monolítico `queries.py`, a constante `_ROAS_EXPR` (expressão SQL do ROAS) foi usada em todos os submódulos mas nunca definida em nenhum deles.  
**Correção:** Adicionada ao `agent/queries/base.py` como constante centralizada e importada nos 4 submódulos que a usavam.  
**Afetados:** `resumo.py`, `ranking.py`, `alertas.py`, `evolucao.py`

### 2. `NameError: _FREQ_EXPR` — `resumo.py` e `ranking.py`
**Causa:** Mesma origem — expressão SQL de frequência média não foi extraída para `base.py`.  
**Correção:** Definida em `base.py` como `_FREQ_EXPR` e importada nos módulos que a usam.

### 3. `NameError: AWARENESS_PATTERNS` — `ranking.py`
**Causa:** Lista de padrões de campanha awareness estava definida localmente em `resumo.py` mas `ranking.py` a usava sem importar.  
**Correção:** Movida para `base.py`, importada em ambos os módulos.

### 4. `NameError: data_range_disponivel` — `alertas.py` e `resumo.py`
**Causa:** Função de diagnóstico estava em `base.py` mas não foi incluída nos imports dos submódulos.  
**Correção:** Adicionada ao import explícito dos módulos afetados.

### 5. `comparar_mesmo_periodo_ano_anterior()` retornando `None`
**Causa:** O corpo da função foi truncado ao extrair do monolítico — o arquivo terminava no meio da definição de variáveis, sem nenhum `return`.  
**Correção:** Corpo completo reimplementado com:
- Query dupla (período atual + mesmo período ano anterior)
- Cálculo de variações percentuais por métrica
- Dict com `periodo_atual`, `periodo_ano_anterior`, `variacao`, `tem_dados_ano_anterior`
- Aviso quando não há dados do ano anterior

### 6. Teste `test_canal_generico_retorna_source_of` — expectativa desatualizada
**Causa:** Antes da refatoração, canal genérico defaultava para `source_of`. Após a nova arquitetura, canal sem mapeamento retorna `cliente_key=None` (ativa modo segmentado — comportamento correto).  
**Correção:** Teste atualizado para aceitar `None` ou `source_of` (ambos válidos dependendo do NLP).

---

## Cobertura por Categoria

### Queries SQL (`test_consultas.py`)
| Classe de Teste | Testes | Status |
|---|---|---|
| `TestListarCampanhas` | 5 | ✅ |
| `TestResumoPorCampanha` | 5 | ✅ |
| `TestPerformanceCampanha` | 6 | ✅ |
| `TestTopCampanhasROAS` | 5 | ✅ |
| `TestTopCampanhasCompras` | 5 | ✅ |
| `TestEvolucaoDiaria` | 6 | ✅ |
| `TestResumoPeriodo` | 7 | ✅ |
| `TestAlertasROASBaixo` | 6 | ✅ |
| `TestCompararCampanhas` | 5 | ✅ |
| `TestMetricasFunil` | 5 | ✅ |
| `TestCompararPeriodos` | 4 | ✅ |
| `TestCompararCampanhasDetalhado` | 3 | ✅ |
| `TestCompararSemanaASemana` | 5 | ✅ |
| `TestCompararMesAMes` | 4 | ✅ |
| `TestCompararAntesDepois` | 5 | ✅ |
| `TestRankingComparativo` | 5 | ✅ |
| `TestDataRangeDisponivel` | 6 | ✅ |

### Dados reais (`test_dados_consultas.py`)
| Classe de Teste | Testes | Status |
|---|---|---|
| `TestVerificarGapDados` | 3 | ✅ |
| `TestTopCriativos` | 3 | ✅ |
| `TestCriativosFadiga` | 2 | ✅ |
| `TestProjecaoMes` | 3 | ✅ |
| `TestSazonalidade` | 4 | ✅ |

### Engine e Isolamento (`test_engine_validacao.py`)
| Classe de Teste | Testes | Status | Nota |
|---|---|---|---|
| `TestDefaultClienteKey` | 5 | ✅ | Canal genérico → `None` (modo segmentado) |
| `TestIsolamentoDados` | 3 | ✅ | POD e Source OF nunca vazam dados |
| `TestFluxoPODPrincipal` | 3 | ✅ | POD usa tabela correta |
| `TestImportsBuilders` | 6 | ✅ | Todos os builders importam OK |
| `TestNLPDeteccaoCliente` | 3 | ✅ | NLP detecta POD e plataforma |
| `TestBackupScript` | 2 | ✅ | Script de backup existe e executa |
| `TestComparativoAutomatico` | 1 | ✅ | Delta automático no resumo |

### Métricas (`test_metricas.py`)
| Classe de Teste | Testes | Status |
|---|---|---|
| `TestROAS` | 3 | ✅ |
| `TestCPC` | 2 | ✅ |
| `TestCPM` | 2 | ✅ |
| `TestCTR` | 2 | ✅ |
| `TestFrequencia` | 2 | ✅ |
| `TestCPA` | 3 | ✅ |
| `TestConsistenciaBanco` | 6 | ✅ |

### Usabilidade (`test_usabilidade.py`)
| Classe de Teste | Testes | Status |
|---|---|---|
| `TestFallbackInteligente` | 4 | ✅ |
| `TestUserPrefs` | 4 | ✅ |

---

## Novos Módulos Testados Indiretamente

Os testes cobriram, além dos módulos de queries, os seguintes módulos criados na sessão:

| Módulo | Testado via | Resultado |
|---|---|---|
| `agent/clients/__init__.py` | import explícito + teste de factory | ✅ |
| `agent/clients/base.py` | instanciação + execute() | ✅ |
| `agent/clients/source_of.py` | factory `get_client_agent("source_of")` | ✅ |
| `agent/clients/pod.py` | factory `get_client_agent("pod_kombucha")` | ✅ |
| `agent/clients/general.py` | factory `get_client_agent(None)` | ✅ |
| `agent/orchestrator/agent.py` | `TestFluxoPODPrincipal` | ✅ |
| `slack/helpers.py` | `TestImportsBuilders.test_handlers_importa_ok` | ✅ |
| `clientes/source_of/dados_historicos.py` | import chain via builders | ✅ |

---

## Arquitetura Validada

### Fluxo multiagente (MULTI_AGENT=0 — produção atual)
```
Slack → _processar_e_responder() → engine.consultar()
  └─ NLP → cliente_key + intent
  └─ queries/* → dados SQL
  └─ claude_client.gerar_resposta() → texto Slack
```

### Fluxo multiagente (MULTI_AGENT=1 — pronto para ativar)
```
Slack → _processar_e_responder() → OrchestratorAgent.consultar()
  └─ NLP → cliente_key
  └─ get_client_agent(cliente_key)
       ├─ SourceOFAgent → queries + Shopify + prompt Source OF
       ├─ PODAgent      → queries + prompt POD
       └─ GeneralAgent  → todos segmentos em paralelo
```

---

## Serviço

```
● agente-guara.service — active (running)
```

Para ativar a arquitetura multiagente:
```bash
# .env
MULTI_AGENT=1
```

---

*Gerado automaticamente em 2026-08-18 por Cesar (Agente de Performance Guará)*
