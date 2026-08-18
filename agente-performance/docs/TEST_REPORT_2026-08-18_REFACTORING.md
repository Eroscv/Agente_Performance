# TEST REPORT — Refactoring Estrutural 2026-08-18

**Projeto:** Agente de Performance Guará  
**Data:** 2026-08-18  
**Responsável:** Cesar (Senior Lead Systems Engineer)  
**Branch:** `main` · Commit final: `1e94625`

---

## Resultado Geral

| Suite | Testes | Passou | Falhou | Tempo |
|---|---|---|---|---|
| **pytest** (regressão automática) | 154 | ✅ 154 | 0 | 13,6s |
| **manual** (integração camadas novas) | 47 | ✅ 47 | 0 | 1,3s |
| **TOTAL** | **201** | **✅ 201** | **0** | — |

---

## O que foi refatorado (e testado)

### Item 1 — `agent/registry.py` extraído de `engine.py`

**O que foi feito:**  
`_carregar_segmentos_ativos()`, `get_contexto_cliente()` e `get_available_campaigns()` foram movidas de `agent/engine.py` para `agent/registry.py`. O `engine.py` re-exporta via alias para compatibilidade total.

**Como foi testado:**

| Teste | Método | Resultado |
|---|---|---|
| `registry.segmentos_ativos` | `carregar_segmentos_ativos()` → conta segmentos | ✅ 3 segmentos |
| `registry.contexto_pod` | `get_contexto_cliente("pod-channel")` | ✅ `pod_kombucha` |
| `registry.contexto_source_of` | `get_contexto_cliente("source-of-ads")` | ✅ `source_of` |
| `registry.contexto_none` | `get_contexto_cliente("geral")` | ✅ `None` |
| `registry.campanhas_disponiveis` | `get_available_campaigns()` → conta campanhas | ✅ 57 campanhas |
| `compat.engine.segmentos` | `from agent.engine import _carregar_segmentos_ativos` | ✅ 3 segmentos |
| `compat.engine.contexto` | `from agent.engine import get_contexto_cliente` | ✅ `pod_kombucha` |

---

### Item 2 — `clientes/*/relatorio/` transformados em aliases

**O que foi feito:**  
`clientes/source_of/relatorio/{diario,semanal,mensal}.py` e `clientes/pod/relatorio/{diario,semanal,mensal}.py` eram wrappers independentes com lógica duplicada. Foram convertidos em aliases de 2 linhas que delegam para `agent/clients/relatorio/`.

**Como foi testado:**

| Teste | Método | Resultado |
|---|---|---|
| `compat.clientes.relatorio` | Import de `clientes/source_of/relatorio/diario.gerar` e `clientes/pod/relatorio/semanal.gerar` | ✅ aliases funcionais |
| Regressão pytest (`test_engine_validacao.py`) | `test_import_builder_*` — 6 testes | ✅ 6/6 |

---

### Item 3 — `agent/queries/comparativos.py` extraído de `comparatives.py`

**O que foi feito:**  
Lógica de comparativos movida de `agent/comparatives.py` (475 linhas) para `agent/queries/comparativos.py`. `agent/comparatives.py` virou alias de re-export.

**Como foi testado:**

| Teste | Método | Resultado |
|---|---|---|
| `compat.comparatives` | `from agent.comparatives import comparar_periodos` é idêntico a `from agent.queries.comparativos import comparar_periodos` | ✅ mesmo objeto |
| Regressão pytest (`test_consultas.py`) | `TestCompararPeriodos`, `TestCompararSemanaASemana`, `TestCompararMesAMes`, `TestCompararAntesDepois`, `TestRankingComparativo` — 20 testes | ✅ 20/20 |

---

### Item 4 — `notion/relatorio.py` separado de `notion/client.py`

**O que foi feito:**  
`get_relatorios_cliente()` e `publicar_relatorio()` foram movidas de `notion/client.py` (infraestrutura HTTP) para `notion/relatorio.py` (lógica de negócio). `notion/client.py` mantém wrappers com lazy import para evitar importação circular.

**Como foi testado:**

| Teste | Método | Resultado |
|---|---|---|
| `compat.notion.lazy` | `from notion.client import get_relatorios_cliente, publicar_relatorio` → callable | ✅ OK |
| Compilação | `py_compile` em `notion/client.py` e `notion/relatorio.py` | ✅ sem erros |
| Regressão pytest | 154 testes sem impacto | ✅ 154/154 |

---

### Etapa 1 — Migração de dados para estrutura por cliente/fonte

**O que foi feito:**  
`data/campanhas.db` (monolítico, 25 MB) foi separado em arquivos por cliente, fonte e periodicidade:

```
data/performance/
  source_of/
    meta_ads.db        ← source_of_meta_ads_diario   (32.193 linhas)
    google_ads.db      ← source_of_gads_diario        (    32 linhas)
    shopify.db         ← source_of_shopify_diario     (   413 linhas)
    shopify_receita.db ← shopify_receita_diaria        (   414 linhas)
  pod_kombucha/
    meta_ads.db        ← pod_kombucha_meta_ads_diario (2.285 linhas)
    google_ads.db      ← pod_kombucha_gads_diario     (1.226 linhas)
data/logs/
  inbox.db             ← consultas_usuarios + thread_context (recebido)
  outbox.db            ← relatorios_log + slack_disparos_log + cron_runs (disparado)
  agent.db             ← nlp_cache + kb_cache + user_prefs (estado interno)
```

**Como foi testado — integridade dos dados migrados:**

| Teste | Tabela | Esperado | Resultado |
|---|---|---|---|
| `integ.sof_meta_rows` | `source_of_meta_ads_diario` | 32.193 | ✅ 32.193 |
| `integ.sof_gads_rows` | `source_of_gads_diario` | 32 | ✅ 32 |
| `integ.pod_meta_rows` | `pod_kombucha_meta_ads_diario` | 2.285 | ✅ 2.285 |
| `integ.pod_gads_rows` | `pod_kombucha_gads_diario` | 1.226 | ✅ 1.226 |
| `integ.shopify_rows` | `shopify_receita_diaria` | 414 | ✅ 414 |
| `conn.inbox` | `consultas_usuarios` | 11 | ✅ 11 |
| `conn.outbox_relatorios` | `relatorios_log` | 24 | ✅ 24 |
| `conn.outbox_cron` | `cron_runs` | 242 | ✅ 242 |
| `conn.outbox_slack` | `slack_disparos_log` | 1 | ✅ 1 |
| `conn.agent_nlp` | `nlp_cache` | 4 | ✅ 4 |
| `conn.agent_kb` | `kb_cache` | 7 | ✅ 7 |
| `conn.agent_prefs` | `user_prefs` | 4 | ✅ 4 |

> `data/campanhas.db` original **não foi removido** — mantido como backup.

---

### Etapa 2 — `db/connections.py`: gerenciador multi-DB com views federadas

**O que foi feito:**  
Criado `db/connections.py` com API pública para abrir DBs por propósito:

- `get_performance_conn(cliente_key, plataforma)` → DB de performance específico
- `get_inbox_conn()` → `data/logs/inbox.db`
- `get_outbox_conn()` → `data/logs/outbox.db`
- `get_agent_conn()` → `data/logs/agent.db`
- `get_federated_conn()` → DB em memória com `ATTACH` dos 5 DBs de performance + TEMP VIEWs: `v_performance`, `campanhas_unificada`, `shopify_receita_diaria`
- `query_fed(sql)` / `query_db(db_key, sql)` → helpers de query

**Como foi testado:**

| Teste | Método | Resultado |
|---|---|---|
| `conn.source_of_meta` | `get_performance_conn("source_of","meta_ads")` | ✅ 32.193 rows |
| `conn.source_of_gads` | `get_performance_conn("source_of","google_ads")` | ✅ 32 rows |
| `conn.pod_meta` | `get_performance_conn("pod_kombucha","meta_ads")` | ✅ 2.285 rows |
| `conn.pod_gads` | `get_performance_conn("pod_kombucha","google_ads")` | ✅ 1.226 rows |
| `fed.v_performance` | `SELECT COUNT(*) FROM v_performance` | ✅ 35.736 rows |
| `fed.campanhas_unificada` | `SELECT COUNT(*) FROM campanhas_unificada` | ✅ 35.736 rows |
| `fed.shopify_receita_diaria` | `SELECT COUNT(*) FROM shopify_receita_diaria` | ✅ 414 rows |
| `fed.segmentos` | GROUP BY cliente_key + plataforma | ✅ 4 segmentos corretos |

**Segmentos federados verificados:**
```
pod_kombucha / google_ads → 1.226 rows
pod_kombucha / meta_ads   → 2.285 rows
source_of    / google_ads →    32 rows
source_of    / meta_ads   → 32.193 rows
```

---

### Etapa 3 — Integração: `db/connection.py` delega para federado + logs roteados

**O que foi feito:**  
- `db/connection.py`: `get_conn()` passa a delegar para `get_federated_conn()` — todo código legado continua funcionando sem alterar uma linha
- Módulos de log roteados para novos DBs:
  - `db/consultas_log.py` → `inbox.db`
  - `db/cron_log.py` → `outbox.db`
  - `db/relatorio_log.py` → `outbox.db`
  - `db/slack_log.py` → `outbox.db`

**Como foi testado:**

| Teste | Método | Resultado |
|---|---|---|
| `legacy.get_conn` | `get_conn().execute("SELECT COUNT(*) FROM v_performance")` | ✅ 35.736 rows |
| `legacy.query` | `query("SELECT * FROM campanhas_unificada LIMIT 5")` | ✅ 5 rows |
| `legacy.query_one` | `query_one("SELECT SUM(investimento) FROM campanhas_unificada WHERE plataforma=?")` | ✅ R$ 3.333.854,63 |
| `log.inbox.consultas` | `listar_consultas()` via `inbox.db` | ✅ 11 consultas |
| `log.outbox.cron` | `listar_runs()` via `outbox.db` | ✅ 100 runs |
| `log.outbox.relatorios` | `listar_relatorios()` via `outbox.db` | ✅ 24 relatórios |
| `log.outbox.slack` | `listar_disparos()` via `outbox.db` | ✅ 1 disparo |

---

### Etapa 4 — Queries organizadas por cliente e fonte

**O que foi feito:**  
Criada estrutura `agent/queries/{source_of,pod_kombucha}/` com atalhos por cliente e plataforma:

```
agent/queries/
  shared/            ← re-exporta todos os módulos compartilhados
  source_of/
    meta_ads.py      ← resumo, top_roas, alertas, criativos, evolucao (pré-fixados)
    google_ads.py    ← resumo, top_roas, evolucao
    shopify.py       ← receita_periodo, diario, status
  pod_kombucha/
    meta_ads.py      ← resumo, top_roas, alertas, criativos, evolucao
    google_ads.py    ← resumo, top_roas, evolucao (⏳ customer_id pendente)
```

`_meta_ads_union_source()` em `base.py` atualizada para usar schemas ATTACHados (`sof_meta.*`, `pod_meta.*`) em vez de nomes de tabela hardcoded.

**Como foi testado com dados reais:**

| Teste | Módulo | Período | Resultado |
|---|---|---|---|
| `q.sof.meta.resumo` | `source_of.meta_ads.resumo()` | ago/2026 | ✅ 5 campanhas |
| `q.sof.meta.top_roas` | `source_of.meta_ads.top_roas()` | ago/2026 | ✅ 2 campanhas |
| `q.sof.meta.criativos` | `source_of.meta_ads.criativos()` via `sof_meta.*` | ago/2026 | ✅ 5 criativos |
| `q.sof.meta.evolucao` | `source_of.meta_ads.evolucao()` | 01–10 ago | ✅ 10 dias |
| `q.sof.gads.resumo` | `source_of.google_ads.resumo()` | jan–ago/2026 | ✅ 4 campanhas |
| `q.sof.gads.evolucao` | `source_of.google_ads.evolucao()` | jan–ago/2026 | ✅ 8 dias |
| `q.sof.shopify.diario` | `source_of.shopify.diario()` | ago/2026 | ✅ 17 dias |
| `q.pod.meta.resumo` | `pod_kombucha.meta_ads.resumo()` | ago/2026 | ✅ 6 campanhas |
| `q.pod.meta.criativos` | `pod_kombucha.meta_ads.criativos()` via `pod_meta.*` | ago/2026 | ✅ 5 criativos |
| `q.pod.gads.resumo` | `pod_kombucha.google_ads.resumo()` | jan/2024–ago/2026 | ✅ 15 campanhas |

---

## Suite de regressão pytest — detalhe por arquivo

| Arquivo | Classe | Testes | Resultado |
|---|---|---|---|
| `test_consultas.py` | `TestListarCampanhas` | 5 | ✅ 5/5 |
| `test_consultas.py` | `TestResumoPorCampanha` | 5 | ✅ 5/5 |
| `test_consultas.py` | `TestPerformanceCampanha` | 6 | ✅ 6/6 |
| `test_consultas.py` | `TestTopCampanhasROAS` | 6 | ✅ 6/6 |
| `test_consultas.py` | `TestTopCampanhasCompras` | 5 | ✅ 5/5 |
| `test_consultas.py` | `TestEvolucaoDiaria` | 6 | ✅ 6/6 |
| `test_consultas.py` | `TestResumoPeriodo` | 7 | ✅ 7/7 |
| `test_consultas.py` | `TestAlertasROASBaixo` | 6 | ✅ 6/6 |
| `test_consultas.py` | `TestCompararCampanhas` | 5 | ✅ 5/5 |
| `test_consultas.py` | `TestMetricasFunil` | 5 | ✅ 5/5 |
| `test_consultas.py` | `TestCompararPeriodos` | 4 | ✅ 4/4 |
| `test_consultas.py` | `TestCompararCampanhasDetalhado` | 3 | ✅ 3/3 |
| `test_consultas.py` | `TestCompararSemanaASemana` | 5 | ✅ 5/5 |
| `test_consultas.py` | `TestCompararMesAMes` | 4 | ✅ 4/4 |
| `test_consultas.py` | `TestCompararAntesDepois` | 5 | ✅ 5/5 |
| `test_consultas.py` | `TestRankingComparativo` | 5 | ✅ 5/5 |
| `test_consultas.py` | `TestDataRangeDisponivel` | 6 | ✅ 6/6 |
| `test_dados_consultas.py` | `TestVerificarGapDados` | 3 | ✅ 3/3 |
| `test_dados_consultas.py` | `TestTopCriativos` | 3 | ✅ 3/3 |
| `test_dados_consultas.py` | `TestCriativosFadiga` | 2 | ✅ 2/2 |
| `test_dados_consultas.py` | `TestProjecaoMes` | 3 | ✅ 3/3 |
| `test_dados_consultas.py` | `TestSazonalidade` | 4 | ✅ 4/4 |
| `test_engine_validacao.py` | `TestDefaultClienteKey` | 5 | ✅ 5/5 |
| `test_engine_validacao.py` | `TestIsolamentoDados` | 3 | ✅ 3/3 |
| `test_engine_validacao.py` | `TestFluxoPODPrincipal` | 3 | ✅ 3/3 |
| `test_engine_validacao.py` | `TestImportsBuilders` | 6 | ✅ 6/6 |
| `test_engine_validacao.py` | `TestNLPDeteccaoCliente` | 3 | ✅ 3/3 |
| `test_engine_validacao.py` | `TestBackupScript` | 2 | ✅ 2/2 |
| `test_metricas.py` | `TestROAS` | 3 | ✅ 3/3 |
| `test_metricas.py` | `TestCPC` | 2 | ✅ 2/2 |
| `test_metricas.py` | `TestCPM` | 2 | ✅ 2/2 |
| `test_metricas.py` | `TestCTR` | 2 | ✅ 2/2 |
| `test_metricas.py` | `TestFrequencia` | 2 | ✅ 2/2 |
| `test_metricas.py` | `TestCPA` | 3 | ✅ 3/3 |
| `test_metricas.py` | `TestConsistenciaBanco` | 6 | ✅ 6/6 |
| `test_usabilidade.py` | `TestFallbackInteligente` | 4 | ✅ 4/4 |
| `test_usabilidade.py` | `TestUserPrefs` | 4 | ✅ 4/4 |
| `test_usabilidade.py` | `TestComparativoAutomatico` | 1 | ✅ 1/1 |

---

## Commits desta sessão

| Commit | Descrição |
|---|---|
| `00f9a77` | refactor: extrair `agent/registry.py` de `engine.py` |
| `f7eed6c` | refactor: `clientes/*/relatorio/` viram aliases de `agent/clients/relatorio/` |
| `a4c4134` | refactor: mover `comparatives` → `queries/comparativos.py` + `publicar_relatorio` → `notion/relatorio.py` |
| `9fe9df2` | feat: `db/connections.py` — gerenciador multi-DB com views federadas |
| `e517dab` | refactor: integrar `db/connections.py` na camada de acesso a dados |
| `1e94625` | refactor: queries organizadas por cliente e fonte |

---

## Arquitetura de dados após refactoring

### Antes

```
data/campanhas.db  ← tudo misturado (25 MB, 19 tabelas, 4 naturezas diferentes)
```

### Depois

```
data/
  campanhas.db              ← ORIGINAL mantido (backup / fallback)
  performance/
    source_of/
      meta_ads.db           ← 32.193 linhas  (Meta Ads Source OF)
      google_ads.db         ←     32 linhas  (Google Ads Source OF)
      shopify.db            ←    413 linhas  (Shopify Source OF diário)
      shopify_receita.db    ←    414 linhas  (receita consolidada)
    pod_kombucha/
      meta_ads.db           ←  2.285 linhas  (Meta Ads POD Kombucha)
      google_ads.db         ←  1.226 linhas  (Google Ads POD ⏳)
  logs/
    inbox.db                ← RECEBIDO: consultas_usuarios, thread_context
    outbox.db               ← DISPARADO: relatorios_log, cron_runs, slack_disparos_log
    agent.db                ← ESTADO: nlp_cache, kb_cache, user_prefs
```

### Arquitetura de queries

```
agent/queries/
  shared/            ← queries agnósticas (v_performance + filtro cliente_key)
  source_of/
    meta_ads.py      ← atalhos Source OF · Meta Ads
    google_ads.py    ← atalhos Source OF · Google Ads
    shopify.py       ← atalhos Source OF · Shopify (exclusivo)
  pod_kombucha/
    meta_ads.py      ← atalhos POD Kombucha · Meta Ads
    google_ads.py    ← atalhos POD Kombucha · Google Ads
```

---

*Gerado automaticamente em 2026-08-18 · 154 pytest + 47 testes manuais · 201/201 PASS*
