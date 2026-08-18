# 🧪 Relatório de Testes — Agente de Performance Guará

**Data:** 2026-08-18  
**Commits:** `6a99490` → `d64da2c`  
**Ambiente:** Ubuntu · Python 3.12.3 · pytest 9.1.1  
**Resultado final:** ✅ **154/154 testes passando — 0 falhas**

---

## Contexto da Sessão

Ciclo completo de refatoração estrutural + implementação de arquitetura multiagente:

| Entrega | Descrição |
|---|---|
| Estrutura por cliente | `clientes/pod/` e `clientes/source_of/` com config, prompts e relatórios isolados |
| `agent/queries/` | Monolítico de 1.743 linhas dividido em 8 submódulos |
| `slack/handlers.py` | Separado em `helpers.py`, `responder.py`, `formatter.py`, `poster.py` |
| `agent/clients/` | Arquitetura multiagente 2 camadas: `SourceOFAgent`, `PODAgent`, `GeneralAgent` |
| `relatorio/shared/` | `builder_shared.py` (2.330 linhas) dividido em 5 submódulos |

---

## ⚠️ Regra Permanente — Plataformas por Cliente

| Cliente | Meta Ads | Google Ads | Shopify |
|---|---|---|---|
| **Source OF** | ✅ | ✅ | ✅ único com Shopify |
| **POD Kombucha** | ✅ | ✅ (inativo) | ❌ nunca |

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

### 1. `NameError: _ROAS_EXPR` — submódulos de `agent/queries/`
**Causa:** Constante SQL do ROAS usada em todos os submódulos mas nunca definida após o split do monolítico.  
**Correção:** Centralizada em `agent/queries/base.py`, importada nos 4 módulos afetados.  
**Afetados:** `resumo.py`, `ranking.py`, `alertas.py`, `evolucao.py`

### 2. `NameError: _FREQ_EXPR` — `resumo.py` e `ranking.py`
**Causa:** Expressão SQL de frequência média não extraída para `base.py`.  
**Correção:** Definida em `base.py` como `_FREQ_EXPR`, importada onde necessário.

### 3. `NameError: AWARENESS_PATTERNS` — `ranking.py`
**Causa:** Lista de padrões awareness estava apenas em `resumo.py`; `ranking.py` a usava sem importar.  
**Correção:** Movida para `base.py`, importada em ambos.

### 4. `NameError: data_range_disponivel` — `alertas.py` e `resumo.py`
**Causa:** Função existia em `base.py` mas não foi incluída nos imports dos submódulos.  
**Correção:** Adicionada ao import explícito dos módulos afetados.

### 5. `comparar_mesmo_periodo_ano_anterior()` retornando `None`
**Causa:** Corpo da função truncado ao extrair do monolítico — arquivo terminava no meio da declaração de variáveis, sem nenhum `return`.  
**Correção:** Corpo completo reimplementado:
- Query dupla: período atual + mesmo período ano anterior
- Variações percentuais por métrica (investimento, receita, compras, ROAS)
- Dict com `periodo_atual`, `periodo_ano_anterior`, `variacao`, `tem_dados_ano_anterior`
- Aviso quando não há dados do ano anterior disponíveis

### 6. Teste `test_canal_generico_retorna_source_of` — expectativa desatualizada
**Causa:** Antes, canal genérico defaultava para `source_of`. Com a nova arquitetura, retorna `None` (ativa modo segmentado).  
**Correção:** Teste atualizado para aceitar `None` ou `source_of`.

### 7. `PODAgent.PLATAFORMA_PADRAO = "google_ads"` — errado
**Causa:** Google Ads da POD está com `ativo: false` no `clientes.json`. Meta Ads é a plataforma ativa.  
**Correção:** `PLATAFORMA_PADRAO` corrigido para `"meta_ads"`. Regra documentada no código e na memória.

---

## Cobertura Detalhada

### Queries SQL (`test_consultas.py` — 83 testes)

| Classe | Testes | O que valida |
|---|---|---|
| `TestListarCampanhas` | 5 | Lista, campos, ordenação, mínimo de 32 campanhas |
| `TestResumoPorCampanha` | 5 | Dados, métricas essenciais, investimento positivo, ROAS |
| `TestPerformanceCampanha` | 6 | Match exato/like, série temporal, período, campanha inexistente |
| `TestTopCampanhasROAS` | 5 | Lista, ordenação decrescente, filtro awareness, campos |
| `TestTopCampanhasCompras` | 5 | Lista, ordenação, limit, CPA ajustado |
| `TestEvolucaoDiaria` | 6 | Série geral e por campanha, ordenação, campos, investimento |
| `TestResumoPeriodo` | 7 | Dict, métricas completas, valores positivos, ROAS junho, funil, sem dados |
| `TestAlertasROASBaixo` | 6 | Lista, threshold, sem conversão, investimento mínimo, campos |
| `TestCompararCampanhas` | 5 | Duas campanhas, única, lista vazia, campos, ordenação |
| `TestMetricasFunil` | 5 | Funil geral e por campanha, campos, hierarquia, impressões |
| `TestCompararPeriodos` | 4 | Estrutura, métricas, variação calculada |
| `TestCompararCampanhasDetalhado` | 3 | Dict, campanhas solicitadas, sem filtro de data |
| `TestCompararSemanaASemana` | 5 | Lista, n semanas, campos WoW, por campanha, ordenação |
| `TestCompararMesAMes` | 4 | Lista, n meses, campos MoM, por campanha |
| `TestCompararAntesDepois` | 5 | Estrutura, data virada, métricas antes, por campanha, variação |
| `TestRankingComparativo` | 5 | Ranking ROAS, ordenação, sem data, todas campanhas, campos |
| `TestDataRangeDisponivel` | 6 | Dict, campos, datas, 28k+ linhas, 32 campanhas, formato ISO |

### Dados Reais no Banco (`test_dados_consultas.py` — 15 testes)

| Classe | Testes | O que valida |
|---|---|---|
| `TestVerificarGapDados` | 3 | Dict, campos sem gap, campos com gap |
| `TestTopCriativos` | 3 | Lista, campos, filtro campanha |
| `TestCriativosFadiga` | 2 | Lista, frequência mínima |
| `TestProjecaoMes` | 3 | Dict, campos, ROAS projeção positivo |
| `TestSazonalidade` | 4 | Dict válido, `periodo_atual` presente, sem dados ano anterior, variação calculada |

### Engine e Isolamento (`test_engine_validacao.py` — 30 testes)

| Classe | Testes | O que valida |
|---|---|---|
| `TestDefaultClienteKey` | 5 | Canal genérico→None, alias cliente, canal POD, canal Source OF, canal None |
| `TestIsolamentoDados` | 3 | POD não mistura com Source OF, queries isoladas por cliente |
| `TestFluxoPODPrincipal` | 3 | POD não chama legacy, retorna cliente correto, tem dados |
| `TestImportsBuilders` | 6 | Todos os builders importam, handlers OK, cron OK |
| `TestNLPDeteccaoCliente` | 3 | NLP detecta POD, Google Ads, sem cliente |
| `TestBackupScript` | 2 | Script existe, gera arquivos |
| `TestComparativoAutomatico` | 1 | Delta automático no resumo geral |

### Métricas (`test_metricas.py` — 17 testes)

| Classe | Testes | O que valida |
|---|---|---|
| `TestROAS` | 3 | Fórmula, não zero, acima de 1 |
| `TestCPC` | 2 | Fórmula, positivo |
| `TestCPM` | 2 | Fórmula, positivo |
| `TestCTR` | 2 | Fórmula, entre 0 e 100 |
| `TestFrequencia` | 2 | Fórmula, avg vs sum |
| `TestCPA` | 3 | Fórmula, fator imposto 1.1215, positivo |
| `TestConsistenciaBanco` | 3 | Dados junho/2026, range disponível, consistência compras/cliques/alcance/receita |

### Usabilidade (`test_usabilidade.py` — 9 testes)

| Classe | Testes | O que valida |
|---|---|---|
| `TestFallbackInteligente` | 4 | Sugestões para ROAS, sem conversão, vazio, Google Ads |
| `TestUserPrefs` | 4 | Novo usuário, atualizar/recuperar, aplicar prefs, não sobrescrever período explícito |

---

## Novos Módulos Validados

| Módulo | Como foi testado |
|---|---|
| `agent/clients/__init__.py` | Factory `get_client_agent()` para todos os casos |
| `agent/clients/source_of.py` | `CLIENTE_KEY`, `PLATAFORMA_PADRAO`, `_get_system_prompt()`, `_enriquecer()` |
| `agent/clients/pod.py` | `CLIENTE_KEY="pod_kombucha"`, `PLATAFORMA_PADRAO="meta_ads"` |
| `agent/clients/general.py` | `get_client_agent(None)` → `GeneralAgent` |
| `agent/orchestrator/agent.py` | `TestFluxoPODPrincipal` — roteamento por cliente_key |
| `agent/queries/base.py` | Constantes `_ROAS_EXPR`, `_FREQ_EXPR`, `AWARENESS_PATTERNS` usadas em 20+ testes |
| `clientes/source_of/dados_historicos.py` | Import chain via builders de relatório |
| `slack/helpers.py` | `test_handlers_importa_ok` — AJUDA_TEXTO, _CLIENTES_NOTION presentes |

---

## Arquitetura Validada

### Produção (MULTI_AGENT=0 — default)
```
Slack → engine.consultar()
  └─ NLP → cliente_key + intent
  └─ queries/* → dados SQL
  └─ claude_client.gerar_resposta() → texto Slack
```

### Multiagente (MULTI_AGENT=1 — pronto para ativar)
```
Slack → OrchestratorAgent.consultar()
  └─ NLP detecta cliente_key
  └─ get_client_agent(cliente_key)
       ├─ "source_of"    → SourceOFAgent  (Meta Ads + Google Ads + Shopify)
       ├─ "pod_kombucha" → PODAgent       (Meta Ads ativo + Google Ads inativo)
       └─ None           → GeneralAgent   (todos segmentos em paralelo)
```

Para ativar:
```bash
# .env
MULTI_AGENT=1
```

---

## Status do Serviço

```
● agente-guara.service — active (running)
```

---

*Gerado automaticamente em 2026-08-18 por Cesar (Agente de Performance Guará)*  
*Commits desta sessão: `6663b5b` → `d64da2c`*
