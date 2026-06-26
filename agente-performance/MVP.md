# 🚀 MVP — Agente de Performance Guará

> Objetivo do MVP: **dados do CSV Meta Ads → relatório publicado automaticamente no Notion**, com métricas recalculadas conforme as regras de negócio definidas com Bianca e Eros.

---

## 🎯 O que o MVP entrega

Um script que você roda (ou agenda), que:

1. Lê os dados do CSV Meta Ads (já carregados no SQLite)
2. Aplica as regras de negócio (imposto, ROAS, CPA, CPC)
3. Publica uma página formatada no Notion com o relatório completo
4. Notifica no Slack com resumo + link da página

**Sem painel externo. Sem integração nova. Apenas os dados que já temos.**

---

## 📐 Regras de Negócio (fixadas em reunião Bianca/Eros)

| Métrica | Como calcular |
|---|---|
| Investimento ajustado | `spend × 1,1215` |
| ROAS | `Receita Shopify ÷ Investimento ajustado` *(nunca usar o do Meta)* |
| CPA | `Investimento ajustado ÷ Nº de compras` *(nunca usar o do Meta)* |
| CAC | `Investimento ajustado ÷ Novos clientes` *(Shopify via UTM)* |
| CPC | **Direto da plataforma Meta** — não recalcular |
| Ticket Médio | `Receita ÷ Transações` *(Shopify)* |

> Fator de imposto: **1,1215** — o valor 1,1225 foi descartado.

---

## 🗺️ Etapas até o MVP

---

### ✅ Etapa 0 — Fundação
**Status: concluída**

O que foi feito:
- CSV Meta Ads normalizado e carregado no SQLite (`data/campanhas.db`)
- Mapeamento de 60+ colunas PT-BR → SQL (`db/schema.py`)
- Loader idempotente: trunca e recarrega ao rodar novamente (`db/loader.py`)
- Regras de negócio implementadas como funções puras (`relatorio/regras.py`)
- Builder de tabelas Meta Ads com regras aplicadas (`relatorio/meta.py`)
  - `tabela_principal()` — métricas por campanha
  - `alocacao_budget()` — % do orçamento por campanha
  - `top_ads()` — anúncios mais investidos
- Cliente Notion funcional (`notion/client.py`) com 5 databases mapeados
- Gerador de páginas Notion (`notion/relatorio.py`) — blocos, tabelas, callouts

**Resultado:** toda a lógica de cálculo e publicação existe. Falta orquestrar.

---

### 🚧 Etapa 1 — Relatório Semanal (próxima entrega)
**Status: em construção**

O que vai ser construído:

**`relatorio/builder.py`** — orquestrador principal
- Lê campanhas do SQLite no período (padrão: última semana)
- Aplica `regras.py` + `meta.py`
- Monta os blocos do relatório
- Chama `notion/relatorio.py` para publicar

**Página Notion gerada automaticamente:**

```
📊 Source OF — Meta Ads · Semana 23/06 → 29/06/2026
├── ⚙️ Callout — gerado pelo Agente de Performance
├── 📊 Resumo Geral da Conta
│   ├── 💰 Investimento total (bruto + ajustado)
│   ├── 👁 Impressões | 🖱 Cliques | 📊 CTR | 💵 CPC (plataforma)
│   └── 📈 ROAS médio recalculado | 📦 Compras | 💰 CPM
├── 📋 Tabela de Campanhas
│   └── Campanha | Invest. | Invest+Imposto | Compras | CPA | ROAS | CPC
├── 💰 Alocação de Budget
│   └── % do orçamento por campanha (ordem decrescente)
├── 🏆 Top Campanhas por ROAS
└── 🔴 Alertas
    └── Campanhas com ROAS < 1 ou sem conversão
```

**`scripts/gerar_relatorio.py`** — CLI de disparo manual
```bash
python scripts/gerar_relatorio.py --cliente source_of --periodo semana
```

**Critério de aceite:** rodar o script → página aparecer no Notion com dados reais.

---

### 📬 Etapa 2 — Disparo via Slack
**Status: planejado**

O que vai ser construído:
- Integração Slack Bolt recebendo perguntas em linguagem natural
- NLP básico para interpretar perguntas (`agent/nlp.py`)
  - *"como foi a semana?"* → relatório semanal
  - *"top campanhas por ROAS"* → ranking
  - *"funil da campanha X"* → métricas de funil
- Engine de consulta SQL dinâmica (`agent/engine.py`)
- Resposta no Slack: resumo em texto + link da página Notion publicada
- Suporte a períodos: hoje, ontem, semana, mês

**Critério de aceite:** perguntar no Slack → receber link Notion em < 10 segundos.

---

### 🔄 Etapa 3 — Agendamento Automático
**Status: planejado**

O que vai ser construído:
- Cron job: relatório publicado toda **segunda-feira às 9h** automaticamente
- Notificação no Slack quando o relatório for publicado
- Suporte a múltiplos clientes simultaneamente
  - Source OF, Consórcio Sorteado, Máximo Serviço, BE MUSHIN, Bazar do Precatório
- Atualização incremental do SQLite (sem recriar do zero)

**Critério de aceite:** segunda-feira → relatório no Notion sem ninguém rodar nada.

---

## 📅 Sequência de entrega

```
Etapa 0 ✅ → Etapa 1 🚧 → Etapa 2 → Etapa 3
(done)       (agora)      (slack)    (auto)
  │              │              │          │
  │         1 script       Slack bot   Cron job
  │         1 página       + link      + multi-cliente
  │         no Notion      Notion
  │
  └─ Base: CSV → SQLite → regras → Notion
```

---

## 🧱 Estrutura do Projeto

```
agente-performance/
├── relatorio/
│   ├── regras.py       ✅ Cálculos com imposto (ROAS, CPA, CAC, CPC)
│   ├── meta.py         ✅ Builder de tabelas Meta Ads
│   └── builder.py      🚧 Orquestrador do relatório semanal
├── notion/
│   ├── client.py       ✅ Cliente HTTP Notion
│   └── relatorio.py    ✅ Gerador de páginas e blocos
├── db/
│   ├── schema.py       ✅ Mapeamento CSV → SQL (60+ colunas)
│   └── loader.py       ✅ Carga idempotente CSV → SQLite
├── agent/
│   ├── engine.py       ✅ Engine de consulta NLP → SQL
│   └── nlp.py          ✅ Interpretação de perguntas
├── slack/
│   └── handlers.py     📋 Handlers Slack (Etapa 2)
├── scripts/
│   └── gerar_relatorio.py  🚧 CLI de disparo (Etapa 1)
└── data/
    ├── campanhas.csv   → arquivo de entrada (upload manual por ora)
    └── campanhas.db    → banco gerado automaticamente
```

**Legenda:** ✅ pronto · 🚧 em construção · 📋 planejado

---

## ⚙️ Como rodar (quando Etapa 1 estiver pronta)

```bash
# 1. Atualizar dados
cp novo_export_meta.csv data/campanhas.csv
python -m db.loader

# 2. Gerar relatório
python scripts/gerar_relatorio.py --cliente source_of --periodo semana

# 3. Abrir link impresso no terminal → página no Notion
```

---

*Guará Marketing — uso interno · Agente de Performance v1 MVP*
