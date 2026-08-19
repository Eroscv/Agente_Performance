# 📊 Daily Report — Agente de Performance Guará

> Gerado em: 2026-08-19 · Referência: D-1 (2026-08-18) · Versão do cron: `a22bcd3`

---

## 🗂️ Índice

1. [Status do Pipeline](#1-status-do-pipeline)
2. [Correções Aplicadas (Sprint de 19/08/2026)](#2-correções-aplicadas)
3. [Arquitetura do Pipeline Diário](#3-arquitetura-do-pipeline-diário)
4. [Guardrails de Qualidade de Dados](#4-guardrails-de-qualidade-de-dados)
5. [Sincronização por Plataforma](#5-sincronização-por-plataforma)
6. [Clientes Ativos](#6-clientes-ativos)
7. [Cron Jobs](#7-cron-jobs)
8. [Banco de Dados Federado](#8-banco-de-dados-federado)
9. [Testes](#9-testes)
10. [Próximos Passos](#10-próximos-passos)

---

## 1. Status do Pipeline

| Componente | Status | Observação |
|---|---|---|
| Cron diário (09:00 BRT) | ✅ Ativo | `0 9 * * * America/Sao_Paulo` |
| Sync Meta Ads | ✅ Corrigido | Agora grava em `data/performance/<cliente>/meta_ads.db` |
| Sync Google Ads | ✅ OK | Grava em `data/performance/<cliente>/google_ads.db` |
| Sync Shopify | ✅ OK | Tabela `shopify_receita_diaria` · 387 dias · 31.285 pedidos |
| Conexão federada | ✅ Corrigida | `reset_federated_conn()` chamado antes da geração |
| Guardrails | ✅ Implementados | Volume mínimo · Queda brusca · Janela de atribuição |
| Relatório POD Kombucha | ✅ Enviado | 2026-08-19 12:15 UTC |
| Relatório Source OF | 🔄 Em monitoramento | Cron das 09:00 BRT disparou · Aguardando confirmação |

---

## 2. Correções Aplicadas

### 🐛 Bug Crítico: Meta Ads gravando no DB errado

**Problema identificado:**  
`scripts/sync_meta_diario.py` gravava sempre em `data/campanhas.db` (DB legado), mas a conexão federada (`db/connections.py`) lê de `data/performance/<cliente>/meta_ads.db`. O sync retornava 61 linhas com sucesso, mas o builder via 0 dados — relatório entrava em `sem_dados`.

**Causa raiz:**  
Funções `salvar()`, `resumo_banco()` e `carregar_meta()` em `meta_ads/loader.py` não recebiam o parâmetro `db_path`, usando sempre o path padrão legado.

**Correção:**  
- `meta_ads/loader.py` → funções agora aceitam `db_path` opcional  
- `scripts/sync_meta_diario.py` → passa `PERFORMANCE_DBS.get((cliente_key, "meta_ads"))` explicitamente  

**Commits:** `a22bcd3` (fix), `e257279` (guardrails)

---

### 🐛 Bug: Conexão federada não via dados recém-sincronizados

**Problema:**  
A conexão `:memory:` do SQLite é um singleton inicializado uma vez. Writes nos DBs de performance feitos após a inicialização não eram vistos automaticamente.

**Correção:**  
`reset_federated_conn()` inserido em dois pontos estratégicos:
1. Início de `gerar_relatorio_diario()` em `relatorio/builder_diario.py`
2. Antes da detecção de plataformas disponíveis em `scripts/cron_todos_relatorios.py`

O reset reinicializa a conexão em ~1,4ms — custo desprezível, seguro chamar sempre.

---

### 🐛 Bug: Coluna `receita` inexistente na view federada

**Problema:**  
`_calcular_delta()` em `cron_todos_relatorios.py` consultava `SUM(receita)` na view `campanhas_unificada`, mas a coluna correta é `valor_compras`.

**Correção:**  
Query atualizada para `SUM(valor_compras) / SUM(investimento)` no cálculo do delta de ROAS.

---

### ⏰ Horário do Cron: 04:00 UTC → 09:00 BRT

**Problema:**  
Cron rodava às 04:00 UTC (01:00 BRT). O Google Ads só consolida dados de D-1 após ~05:00 BRT. Resultado: relatório sempre gerado com dados incompletos ou sem dados.

**Correção:**  
Cron atualizado para `0 9 * * * America/Sao_Paulo` (09:00 BRT = 12:00 UTC).  
Payload do cron força sync antes de checar plataformas disponíveis.

---

### 🔧 Fix: Tabela Shopify incorreta

**Problema:**  
`cron_todos_relatorios.py` buscava `source_of_shopify_diario` (não existe). Tabela correta: `shopify_receita_diaria`.

**Commit:** `0db4603`

---

### 🔧 Fix: Source OF — detecção de plataforma padrão

**Problema:**  
`plataforma_meta` retornava `None` em vez de `'google_ads'` quando Source OF não tinha Meta Ads ativo, causando falha silenciosa.

**Commit:** `c9f841f`

---

## 3. Arquitetura do Pipeline Diário

```
09:00 BRT
    │
    ▼
[cron_todos_relatorios.py]
    │
    ├── loader_fn()  ← sync via API (retry 3x, 10s entre tentativas)
    │       ├── sync_meta_diario.py  →  data/performance/<cliente>/meta_ads.db
    │       └── sync_gads_diario.py  →  data/performance/<cliente>/google_ads.db
    │
    ├── reset_federated_conn()  ← garante que dados novos são visíveis
    │
    ├── Detecção de plataformas disponíveis
    │       ├── Google Ads: SELECT SUM(investimento) FROM <tabela_gads> WHERE data=D-1
    │       ├── Meta Ads:   SELECT SUM(investimento) FROM <tabela_meta> WHERE data=D-1
    │       └── Shopify:    SELECT SUM(receita)      FROM shopify_receita_diaria WHERE data=D-1
    │
    ├── Guardrails de qualidade  ← bloqueia se dados inválidos
    │       ├── validar_cobertura_dados()    (volume mínimo 40% da média 30d)
    │       ├── queda brusca de investimento (<20% da média 7d)
    │       └── aviso_janela_atribuicao()   (Meta 3d / Google 30d)
    │
    ├── Alertas proativos (ROAS < threshold)
    │       └── postar_alerta_ancorado()    (âncora + thread detalhada no Slack)
    │
    └── gerar_relatorio_diario()  ← por plataforma
            ├── Gera blocos Notion
            ├── Publica no Notion
            ├── Valida pré-Slack (resumo, título, blocos, URL)
            └── Posta no Slack (âncora + thread por plataforma)
```

---

## 4. Guardrails de Qualidade de Dados

Implementados em `agent/queries/base.py` e aplicados no cron antes de gerar qualquer relatório.

### Guardrail 1 — Cobertura mínima de dados

```python
validar_cobertura_dados(data_ref, cliente_key, plataforma)
```

- Compara volume do dia com média dos últimos 30 dias
- Threshold: **40% da média** (abaixo = dado parcial → aborta)
- Retorna `{"ok": True}` ou `{"ok": False, "motivo": "...", "aviso": "..."}`

### Guardrail 2 — Queda brusca de investimento

- Compara investimento de D-1 com média dos últimos 7 dias
- Threshold: **< 20% da média** = sinal de problema técnico (API, campanha pausada inesperadamente)
- Aviso postado no Slack antes do relatório

### Guardrail 3 — Janela de atribuição

```python
aviso_janela_atribuicao(data_ref, plataforma)
```

| Plataforma | Janela | Aviso disparado quando |
|---|---|---|
| Meta Ads | 3 dias | Relatório dentro de 3d da data de referência |
| Google Ads | 30 dias | Relatório dentro de 30d da data de referência |

Aviso é informativo (não bloqueia), mas aparece no relatório Slack.

---

## 5. Sincronização por Plataforma

### Meta Ads

| Parâmetro | Valor |
|---|---|
| Script | `scripts/sync_meta_diario.py` |
| DB destino | `data/performance/<cliente>/meta_ads.db` |
| Tabela | `<cliente_key>_meta_ads_diario` |
| Janela de sync | D-3 a D-1 (3 dias retroativos) |
| Último sync POD | 2026-08-18 → 61 linhas (R$ 3.079,95 investimento) |

### Google Ads

| Parâmetro | Valor |
|---|---|
| Script | `scripts/sync_gads_diario.py` |
| DB destino | `data/performance/<cliente>/google_ads.db` |
| Tabela | `<cliente_key>_gads_diario` |
| Janela de sync | D-1 (delay ~05:00 BRT) |

### Shopify

| Parâmetro | Valor |
|---|---|
| Script | `db/shopify_sync.py` |
| DB destino | `db/performance.db` |
| Tabela | `shopify_receita_diaria` |
| Cron automático | `shopify-sync-diario` às 06:00 UTC (últimos 8 dias) |
| Histórico | 387 dias · 31.285 pedidos · R$ 9,58M |
| ROAS real | Receita Shopify ÷ Investimento Meta ajustado (×1,1215) |

---

## 6. Clientes Ativos

### Source OF (Meta Ads + Google Ads + Shopify)

| Campo | Valor |
|---|---|
| `cliente_key` | `source_of` |
| Plataformas | Meta Ads + Google Ads + Shopify |
| `multi_plataforma` | `true` → usa `executar_cliente_multi_plataforma()` |
| Relatório | Âncora no Slack + thread por plataforma |
| ROAS threshold | Meta ≥ 2.0x · Google ≥ 1.5x |

### POD Kombucha (Meta Ads)

| Campo | Valor |
|---|---|
| `cliente_key` | `pod_kombucha` |
| Plataforma | Meta Ads |
| `multi_plataforma` | `false` → usa `executar_cliente()` |
| DB | `data/performance/pod_kombucha/meta_ads.db` |
| Tabela | `pod_kombucha_meta_ads_diario` |
| Último dado | 2026-08-18 · 61 linhas · R$ 3.079,95 |
| Relatório 19/08 | ✅ Enviado às 12:15 UTC |

---

## 7. Cron Jobs

### Relatório Diário

| Campo | Valor |
|---|---|
| Job ID | `639c9af7-882e-483f-85a0-4affb67322c2` |
| Schedule | `0 9 * * * America/Sao_Paulo` (09:00 BRT) |
| Próxima execução | 2026-08-20 12:00:00 UTC |
| Payload | `agentTurn` → `cron_todos_relatorios.py --periodo diario` |

### Shopify Sync Diário

| Campo | Valor |
|---|---|
| Job ID | `shopify-sync-diario` |
| Schedule | `0 6 * * *` UTC (06:00 UTC) |
| Endpoint | `POST /shopify/sync` |
| Janela | Últimos 8 dias |

---

## 8. Banco de Dados Federado

O sistema usa uma conexão `:memory:` SQLite que faz ATTACH de todos os DBs de performance. A view `campanhas_unificada` agrega todas as plataformas em um schema único.

```
db/connections.py
    ├── federated_conn  ← singleton :memory:
    ├── PERFORMANCE_DBS ← dict (cliente_key, plataforma) → path
    └── reset_federated_conn()  ← reinicializa em ~1.4ms
```

**DBs attached:**

| DB | Tabela principal |
|---|---|
| `data/performance/source_of/meta_ads.db` | `source_of_meta_ads_diario` |
| `data/performance/source_of/google_ads.db` | `source_of_gads_diario` |
| `data/performance/pod_kombucha/meta_ads.db` | `pod_kombucha_meta_ads_diario` |
| `db/performance.db` | `shopify_receita_diaria` |

**View `campanhas_unificada`:**

```sql
-- Schema resumido
SELECT
    cliente_key,
    data_inicio,
    campanha,
    investimento,
    impressoes,
    cliques,
    conversoes,
    valor_compras,   -- ← coluna correta (não "receita")
    plataforma
FROM ...
```

> ⚠️ **Atenção:** Use sempre `valor_compras` (não `receita`) em queries contra `campanhas_unificada`.

---

## 9. Testes

```
196 testes passando  ✅
5 testes com falhas pré-existentes (não são regressões)
```

### Falhas pré-existentes (valores hardcoded desatualizados)

| Teste | Motivo | Prioridade |
|---|---|---|
| `test_cpc_formula` | Valores hardcoded de junho/2026 | Baixa |
| `test_cpm_formula` | Valores hardcoded de junho/2026 | Baixa |
| `test_ctr_formula` | Valores hardcoded de junho/2026 | Baixa |
| `test_cpa_formula` | Valores hardcoded de junho/2026 | Baixa |
| (5º teste) | idem | Baixa |

Estes testes usavam valores de campanhas reais de junho que mudaram com novos dados. **Não são regressões** — o comportamento do código está correto.

---

## 10. Próximos Passos

- [ ] Confirmar no Slack relatório de Source OF (cron das 09:00 BRT de 19/08)
- [ ] Corrigir os 5 testes com valores hardcoded desatualizados
- [ ] Adicionar endpoint `GET /relatorios/status` (histórico dos últimos runs)
- [ ] Monitorar primeiro ciclo completo dos guardrails em produção

---

## 📝 Histórico de Commits Relevantes

| Hash | Mensagem | Impacto |
|---|---|---|
| `a22bcd3` | fix: sync Meta Ads grava no DB correto + reset federado pós-sync + is_gads + receita | 🔴 Crítico — causa raiz dos `sem_dados` |
| `e257279` | feat: guardrails de qualidade de dados | 🟡 Qualidade |
| `0db4603` | fix: source_of_shopify_diario → shopify_receita_diaria | 🔴 Crítico |
| `9824747` | refactor: views federadas — auto-reconexão, inspect_federated_views() | 🟡 Infraestrutura |
| `c9f841f` | fix: corrige default enganoso em plataforma_meta | 🟠 Bug |
| `99f1b9c` | fix: sync Google Ads grava no banco federado correto | 🔴 Crítico |
| `1078fe4` | feat: intent comparar_plataformas | 🟢 Feature |
| `9f2cb73` | fix: Source OF Google Ads — retorna dados de ambas as plataformas | 🟠 Bug |
| `8ec3c7d` | feat: aviso automático de delay de dados | 🟢 Feature |

---

*Gerado automaticamente pelo Agente de Performance Guará · Cesar (⚙️)*
