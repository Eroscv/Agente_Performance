# ⚙️ Agente de Performance Guará — Status

> Atualizado em: 25/09/2026 16:52 UTC

---

## 🔴 Bloqueantes ativos

Dois tokens expirados estão impedindo os syncs desde ~20/09/2026:

| Integração | Erro | Conta / Loja |
|---|---|---|
| **Meta Ads** | `Malformed access token` | `act_324906945710366` |
| **Shopify POD Kombucha** | `401 Unauthorized` | `d5bead-1b.myshopify.com` |

**Impacto em cascata:**
- Sync Meta falha → relatórios caem em `sem_dados`
- Alertas de ROAS continuavam disparando com dados velhos no banco
- Todos os 15 crons foram desativados manualmente em 25/09 ~12:55 UTC para parar o ruído

---

## ⚪ Estado atual (25/09/2026)

Todos os crons estão **desativados**. Nenhum sync, relatório ou alerta está rodando.

| Cron | Status |
|---|---|
| gads-sync-horario | ⏸ Desativado |
| meta-sync-horario | ⏸ Desativado |
| shopify-sync-horario | ⏸ Desativado |
| pod-shopify-sync-diario | ⏸ Desativado |
| sheets-sync-sqlite | ⏸ Desativado |
| etl-warehouse-incremental | ⏸ Desativado |
| Relatório Diário — Todos os Clientes | ⏸ Desativado |
| Relatório Completo Source OF — Manhã | ⏸ Desativado |
| Relatório Pod Kombucha — Manhã | ⏸ Desativado |
| Relatório Completo Source OF — Tarde | ⏸ Desativado |
| Relatório Pod Kombucha — Tarde | ⏸ Desativado |
| Relatório Semanal — Todos os Clientes | ⏸ Desativado |
| Relatório Mensal — Todos os Clientes | ⏸ Desativado |
| agente-performance: atualizar CONSULTAS.md | ⏸ Desativado |
| Backup BD Agente Guará | ⏸ Desativado |

---

## ✅ O que estava funcionando antes

- Google Ads sync (Source OF) — normal
- Shopify sync (Source OF) — normal
- ETL Warehouse (SQLite → Parquet) — normal
- Slack bot (consultas ao vivo) — normal

---

## 📋 Próximos passos

### 1. Renovar tokens _(bloqueante — fazer primeiro)_

**Meta Ads**
1. Acessar o Meta Business Manager da conta `act_324906945710366`
2. Gerar novo token de System User com permissões `ads_read` + `ads_management`
3. Atualizar `META_ACCESS_TOKEN` no arquivo `.env`

**Shopify POD Kombucha**
1. Acessar o painel da loja `d5bead-1b.myshopify.com` → Apps → API
2. Revogar token antigo e gerar novo com escopo `read_orders`
3. Atualizar `SHOPIFY_POD_ACCESS_TOKEN` no arquivo `.env`

---

### 2. Validar syncs manualmente

```bash
cd /home/ubuntu/.openclaw/workspace/agente-performance

# Meta Ads — testar sync manual
venv/bin/python scripts/sync_meta_diario.py --dias 5

# POD Kombucha Shopify — testar sync manual
venv/bin/python scripts/sync_pod_shopify.py --dias 5
```

Confirmar que ambos gravam linhas sem erro antes de reativar os crons.

---

### 3. Reativar syncs horários

Após tokens validados, reativar os 4 crons de sync:

- `gads-sync-horario`
- `meta-sync-horario`
- `shopify-sync-horario`
- `pod-shopify-sync-diario`

---

### 4. Reativar relatórios

Após 1 ciclo completo de sync limpo (aguardar ~1h), reativar:

- `Relatório Diário — Todos os Clientes`
- `Relatório Semanal — Todos os Clientes`
- `Relatório Mensal — Todos os Clientes`
- Relatórios Source OF e POD (manhã + tarde)

---

### 5. Decidir sobre alertas ROAS

Os alertas de ROAS foram desativados por disparo excessivo. Antes de reativar, avaliar:

- [ ] Só alertar se ROAS cair **2 dias consecutivos** (não apenas 1)
- [ ] Aumentar threshold mínimo de R$ 100 de investimento para R$ 300
- [ ] Limitar a **1 alerta por cliente por dia** (já existe deduplicação, revisar lógica)

---

### 6. Demais crons (baixa prioridade)

Reativar após estabilização dos relatórios:

- `sheets-sync-sqlite`
- `etl-warehouse-incremental`
- `agente-performance: atualizar CONSULTAS.md`
- `Backup BD Agente Guará`

---

## 🏗️ Arquitetura atual

```
Syncs horários (Meta / GAdS / Shopify)
    ↓
data/performance/<cliente>/<plataforma>.db  (SQLite por cliente)
    ↓
ETL Warehouse → Parquet (star schema DuckDB)
    ↓
cron_todos_relatorios.py --periodo diario
    ├── Guardrails de qualidade (volume mínimo, queda brusca, janela atribuição)
    ├── Alertas proativos ROAS (< threshold)
    ├── Builder de blocos por plataforma
    ├── Publicação no Notion (1 página por cliente/data)
    └── Post no Slack (âncora + thread por plataforma + link Notion)
```

**Stack:** FastAPI + Slack Bolt + SQLite + DuckDB + Parquet + Claude Haiku 4.5 + Notion API

**Clientes ativos:** Source OF (Meta + Google + Shopify) · POD Kombucha (Meta + Shopify)

---

## 📁 Estrutura de diretórios relevante

```
agente-performance/
├── scripts/
│   ├── cron_todos_relatorios.py   ← orquestrador principal
│   ├── sync_meta_diario.py        ← sync Meta Ads
│   ├── sync_gads_diario.py        ← sync Google Ads
│   ├── cron_shopify_sync.py       ← sync Shopify Source OF
│   └── sync_pod_shopify.py        ← sync Shopify POD Kombucha
├── relatorio/
│   ├── builder_diario.py          ← builder relatório diário
│   ├── builder_mensal.py          ← builder relatório mensal
│   └── regras.py                  ← FATOR_IMPOSTO (1.1215), thresholds
├── data/performance/
│   ├── source_of/
│   │   ├── meta_ads.db
│   │   ├── google_ads.db
│   │   └── shopify_receita.db
│   └── pod_kombucha/
│       ├── meta_ads.db
│       └── shopify_receita.db
└── .env                           ← tokens e credenciais
```

---

*Gerado por Cesar ⚙️ — Agente de Performance Guará*
