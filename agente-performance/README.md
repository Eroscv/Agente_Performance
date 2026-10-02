# ⚙️ Agente de Performance Guará

> Analista de mídia paga com IA — Meta Ads + Google Ads + Shopify — para os clientes da **Guará Marketing**.

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-green.svg)](https://fastapi.tiangolo.com)
[![Claude](https://img.shields.io/badge/Claude-Haiku_4.5-orange.svg)](https://anthropic.com)
[![Slack](https://img.shields.io/badge/Slack-Bolt-blueviolet.svg)](https://slack.dev/bolt-python)
[![Notion](https://img.shields.io/badge/Notion-API-black.svg)](https://developers.notion.com)

---

## 📌 O que é

O **Agente de Performance Guará** é um sistema de análise de campanhas publicitárias acionado por linguagem natural via Slack. Ele consome dados diretamente das APIs de **Meta Ads**, **Google Ads** e **Shopify**, armazena tudo em SQLite local, responde perguntas em português com métricas calculadas pelas **regras de negócio da Guará**, e publica relatórios automáticos no Notion e Slack.

---

## 🗂️ Estrutura do Projeto

```
agente-performance/
├── main.py                  # Entry point FastAPI + Slack Bolt
├── clientes.json            # Registry de clientes (sem mexer em código)
├── requirements.txt
├── .env.example
│
├── agent/                   # Núcleo do agente conversacional
│   └── comparatives.py      # Comparações semana-a-semana
│
├── api/                     # Endpoints REST (FastAPI)
│
├── db/                      # Camada de dados SQLite
│   ├── connection.py        # Conexão singleton
│   ├── schema.py            # Criação de tabelas
│   ├── registry.py          # Registro de tabelas por cliente
│   ├── validator.py         # Validações de integridade dos dados
│   ├── cron_log.py          # Log de execuções de cron
│   ├── shopify_sync.py      # Sync Shopify → SQLite
│   └── legacy/              # Loaders legados (backup histórico)
│
├── meta_ads/                # Integração Meta Ads API
│   ├── client.py            # (via SDK oficial)
│   └── loader.py            # Carrega campanhas → SQLite
│
├── google_ads/              # Integração Google Ads API
│   ├── client.py            # (via google-ads SDK)
│   └── loader.py            # Carrega campanhas → SQLite
│
├── shopify/                 # Integração Shopify API
│   └── client.py            # Pedidos, receita, SKUs, recompras
│
├── relatorio/               # Builders de relatório
│   ├── builder_shared.py    # Blocos reutilizáveis (Notion Block Kit)
│   ├── builder_semanal.py   # Relatório semanal
│   ├── builder_mensal.py    # Relatório mensal
│   ├── builder_diario.py    # Relatório diário
│   ├── builder_periodo.py   # Relatório por período customizado
│   ├── builder_pod.py       # Relatório POD Kombucha (Google Ads)
│   ├── regras.py            # Regras de negócio da Guará
│   └── meta.py              # Metadados de relatório
│
├── notion/                  # Integração Notion API
│   ├── client.py
│   └── historico.py         # Publica relatórios no Histórico
│
├── slack/                   # Handlers Slack Bolt
│
├── scripts/                 # Scripts utilitários e crons
│   ├── sync_gads_diario.py  # Sync Google Ads → SQLite (cron diário)
│   ├── sync_meta_diario.py  # Sync Meta Ads → SQLite (cron diário)
│   ├── cron_relatorio.py    # Dispara relatório via cron
│   ├── cron_todos_relatorios.py
│   ├── cron_shopify_sync.py
│   ├── relatorio_diario.py
│   └── migrar_tabelas.py    # Migração de tabelas legadas
│
├── utils/                   # Utilitários gerais
│
├── tests/                   # Testes automatizados
│
└── data/
    └── campanhas.db         # Banco SQLite local (não vai ao repositório)
```

---

## 🏗️ Arquitetura

```
Slack ──► FastAPI ──► Agent (Claude Haiku 4.5)
                          │
              ┌───────────┼───────────────┐
              ▼           ▼               ▼
          Meta Ads    Google Ads       Shopify
          API          API             API
              │           │               │
              └───────────┴───────────────┘
                          │
                     SQLite Local
                   (campanhas.db)
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
           Notion               Slack (relatórios)
         (Histórico)
```

### Fluxo de dados

1. **Crons diários** (07h e 07h30 UTC) sincronizam Meta Ads e Google Ads → SQLite
2. **Shopify Sync** (06h UTC) sincroniza pedidos dos últimos 8 dias → tabela `shopify_receita_diaria`
3. **Builders** leem do SQLite e geram blocos Notion Block Kit
4. **Relatórios automáticos** são publicados no Notion e postados no Slack

---

## 📊 Banco de Dados (SQLite)

### Padrão de nomenclatura
```
{cliente_key}_{plataforma}_diario
```

### Tabelas por cliente

| Tabela | Conteúdo |
|---|---|
| `source_of_meta_ads_diario` | Campanhas Meta Ads — Source OF |
| `source_of_gads_diario` | Campanhas Google Ads — Source OF |
| `source_of_shopify_diario` | Vendas Shopify — Source OF |
| `pod_kombucha_gads_diario` | Campanhas Google Ads — POD Kombucha |
| `shopify_receita_diaria` | Receita diária consolidada Shopify |

### Views

| View | Descrição |
|---|---|
| `v_performance` | Visão unificada Meta + Google por período |
| `campanhas_unificada` | Todas as campanhas de todos os clientes |

### Shopify Sync
- **387 dias** sincronizados, **31.285 pedidos**, **R$ 9,58M** em receita
- ROAS real calculado: `Receita Shopify ÷ (Investimento Meta × 1,1215)`
- Fator `1.1215` = impostos sobre investimento em mídia

---

## 👥 Clientes Configurados

| Cliente | Meta Ads | Google Ads | Shopify |
|---|---|---|---|
| **Source OF** | ✅ `act_1059884611898142` | ✅ `4311260590` | ✅ |
| **POD Kombucha** | ✅ configurado | 🔜 aguardando customer_id | — |

Novos clientes: adicionar bloco em `clientes.json` — **sem mexer em código**.

---

## 📋 Relatórios

### Tipos disponíveis

| Builder | Função | Descrição |
|---|---|---|
| `builder_semanal.py` | `gerar_relatorio_semanal` | Análise das últimas semanas do mês |
| `builder_mensal.py` | `gerar_relatorio_mensal` | Fechamento mensal completo |
| `builder_diario.py` | `gerar_relatorio_diario` | Resumo diário de campanhas |
| `builder_periodo.py` | `gerar_relatorio_periodo` | Período customizado (data_inicio/data_fim) |
| `builder_pod.py` | `gerar_relatorio_pod` | Relatório especializado POD Kombucha |

### Seções do relatório semanal/mensal

1. **Análise da última semana** — comparativo semana atual vs. anterior
2. **Visão mensal diária** — evolução dia a dia
3. **Visão mensal narrativa** — texto gerado por IA (Claude)
4. **Metas do mês** — atingimento vs. meta definida
5. **Tendência mensal** — projeção de fechamento
6. **Próximos passos** — recomendações geradas por IA

### Publicação

Todos os builders aceitam `publicar=False` para teste sem efeitos externos:

```python
from relatorio.builder_semanal import gerar_relatorio_semanal
blocos = gerar_relatorio_semanal("source_of", publicar=False)
print(f"{len(blocos)} blocos gerados")
```

---

## 📡 Pipeline de Dashboard — Atualização em Tempo Real

O agente mantém um pipeline de ingestão contínua que envia os dados consolidados (Meta Ads + Google Ads + Shopify) para o dashboard externo via API REST (Supabase). O pipeline foi desenhado para:

- **Baixo volume por envio** — só envia datas novas, nunca reprocessa o histórico inteiro
- **Alta frequência** — atualização a cada 15 minutos durante o dia
- **Rastreabilidade** — cada envio é registrado com timestamp em `data/dashboard_sync.db`
- **Observabilidade** — crescimento das tabelas é monitorado em `data/dashboard_growth.db`

### ✅ Checklist de Qualidade do Pipeline

#### [x] Coleta a cada 15 minutos

O script `scripts/dashboard_ingest.py` é acionado pelo cron `dashboard-ingest-15min` nos minutos `:02/:17/:32/:47` de cada hora — com offset para não colidir com os syncs de fonte (Meta nos `:00/:15/:30/:45`, Google nos `:05/:20/:35/:50`, Shopify nos `:10/:25/:40/:55`).

```
:00  Meta sync        :02  Dashboard ingest
:05  GAds sync        :10  Shopify sync
:15  Meta sync        :17  Dashboard ingest
:20  GAds sync        :25  Shopify sync
...
```

#### [x] Empilhar só os dados novos, sem refazer a janela do dia

A lógica de `_desde_para()` é **adaptativa**:

| Situação | Janela retroativa | Motivo |
|---|---|---|
| Último sync há < 2h | 2 horas | Evita reenviar o dia inteiro em cada ciclo de 15min |
| Último sync há ≥ 2h | 2 dias | Segurança para pegar dados tardios da API (Meta/Google atualizam retroativamente) |
| Primeira execução | 30 dias | Carga inicial completa |

O estado de cada plataforma é persistido em `data/dashboard_sync.db`:

```sql
CREATE TABLE sync_state (
    cliente    TEXT NOT NULL,
    plataforma TEXT NOT NULL,
    last_sync  TEXT NOT NULL,  -- ISO timestamp UTC
    PRIMARY KEY (cliente, plataforma)
);
```

#### [x] Monitorar o crescimento das tabelas

Após cada sync bem-sucedido, `snapshot_table_growth()` registra o tamanho atual de cada tabela em `data/dashboard_growth.db`:

```sql
CREATE TABLE table_growth (
    ts         TEXT NOT NULL,
    cliente    TEXT NOT NULL,
    plataforma TEXT NOT NULL,
    row_count  INTEGER NOT NULL,
    PRIMARY KEY (ts, cliente, plataforma)
);
```

Tabelas monitoradas:

| Cliente | Plataforma | Tabela SQLite |
|---|---|---|
| Source OF | meta_ads | `source_of_meta_ads_diario` |
| Source OF | google_ads | `source_of_gads_diario` |
| Source OF | shopify | `shopify_receita_diaria` |
| POD Kombucha | pod_meta_ads | `pod_kombucha_meta_ads_diario` |
| POD Kombucha | pod_google_ads | `pod_kombucha_gads_diario` |

Para ver o relatório de crescimento:

```bash
python scripts/dashboard_ingest.py --growth
# Saída:
# === Crescimento das tabelas (últimos 7 dias) ===
#   Source OF/meta_ads: 34,100 → 34,838 (+738 linhas | 96 snaps)
#   Source OF/shopify:  451 → 459 (+8 linhas | 96 snaps)
#   ...
```

#### [x] Validar os dados batendo com o relatório semanal

O comando `--validate` compara os totais locais (SQLite) e exibe investimento, receita e ROAS por plataforma — usado para cruzar com o relatório semanal gerado pelo `cron_relatorio.py`:

```bash
python scripts/dashboard_ingest.py --validate
# Saída:
# === Validação local x dashboard (últimos 7 dias — desde 2026-09-25) ===
#   Source OF / Meta Ads: 7 dias | Inv R$45,201 | Receita R$168,698 | ROAS 3.73
#   Source OF / Google Ads: 7 dias | Inv R$3,209 | Receita R$17,577 | ROAS 5.48
#   POD Kombucha / Meta Ads: 7 dias | Inv R$33,501 | Receita R$149,233 | ROAS 4.45
#   Source OF / Shopify: 8 dias | R$222,121 | 660 pedidos
```

Um cron semanal (`dashboard-growth-weekly`, toda segunda às 9h BRT) roda automaticamente `--validate` + `--growth` e avisa se detectar divergências.

### Comandos CLI do `dashboard_ingest.py`

```bash
# Sync normal (incremental inteligente)
python scripts/dashboard_ingest.py

# Forçar reenvio de todos os dados (ignora last_sync)
python scripts/dashboard_ingest.py --force

# Lookback customizado
python scripts/dashboard_ingest.py --dias 7

# Simular sem enviar
python scripts/dashboard_ingest.py --dry-run

# Filtrar plataforma
python scripts/dashboard_ingest.py --plat meta_ads

# Ver relatório de crescimento
python scripts/dashboard_ingest.py --growth

# Validar dados locais vs dashboard
python scripts/dashboard_ingest.py --validate

# Só registrar snapshot (sem sync)
python scripts/dashboard_ingest.py --snapshot
```

---

## ⏰ Crons Configurados

| Horário (BRT) | Nome | Descrição |
|---|---|---|
| :00/:15/:30/:45 | `meta-sync-horario` | Sync Meta Ads → SQLite (últimos 2 dias) |
| :05/:20/:35/:50 | `gads-sync-horario` | Sync Google Ads → SQLite (últimos 2 dias) |
| :10/:25/:40/:55 | `shopify-sync-horario` | Sync Shopify → SQLite (últimos 2 dias) |
| :02/:17/:32/:47 | `dashboard-ingest-15min` | Ingestão incremental → Dashboard |
| 06h00 | `shopify-sync-diario` | Sync Shopify histórico (últimos 8 dias) |
| 09h00 (seg) | `dashboard-growth-weekly` | Validação + crescimento de tabelas |
| 09h00 (seg) | `flora-learning-report-semanal` | Relatório de aprendizado Flora |
| 02h00 (seg) | `flora-relatorio-semanal` | Relatório semanal Flora |
| 02h00 (dia 1) | `flora-relatorio-mensal` | Relatório mensal Flora |

---

## 🚀 Como rodar

### Pré-requisitos

- Python 3.12+
- Acesso às APIs: Meta Ads, Google Ads, Shopify, Notion, Slack, Anthropic

### Instalação

```bash
# Clone o repositório
git clone https://github.com/Eroscv/Flora-Agente.git
cd agente-performance

# Crie o ambiente virtual
python3 -m venv venv
source venv/bin/activate

# Instale as dependências
pip install -r requirements.txt
pip install google-ads python-dateutil

# Configure as variáveis de ambiente
cp .env.example .env
# Edite .env com suas credenciais
```

### Variáveis de ambiente necessárias

```env
# Anthropic (Claude)
ANTHROPIC_API_KEY=

# Slack
SLACK_SIGNING_SECRET=
SLACK_BOT_TOKEN=

# Notion
NOTION_API_KEY=
NOTION_DATABASE_ID=

# Meta Ads
META_APP_ID=
META_APP_SECRET=
META_ACCESS_TOKEN=

# Google Ads
GOOGLE_ADS_DEVELOPER_TOKEN=
GOOGLE_ADS_CLIENT_ID=
GOOGLE_ADS_CLIENT_SECRET=
GOOGLE_ADS_REFRESH_TOKEN=
GOOGLE_ADS_LOGIN_CUSTOMER_ID=

# Shopify
SHOPIFY_STORE=
SHOPIFY_TOKEN=

# App
PORT=8001
DB_PATH=data/campanhas.db
CLAUDE_MODEL=claude-haiku-4-5
```

### Rodando o servidor

```bash
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8001
```

### Systemd service

```bash
# O serviço está configurado como agente-guara.service
sudo systemctl status agente-guara.service
sudo systemctl restart agente-guara.service
```

---

## 🔌 Integrações

### Meta Ads API
- SDK oficial `facebook-business`
- Coleta por lotes de 14 dias (limite de paginação da API)
- Campos: investimento, impressões, cliques, compras, ROAS, CPM, CTR

### Google Ads API
- SDK oficial `google-ads`
- Relatório GAQL por campanha/dia
- Campos: investimento, impressões, cliques, conversões, valor de conversão

### Shopify API
- REST Admin API (versão 2024-01)
- Endpoint `/orders.json` com paginação por cursor
- Sync dos últimos N dias configurável
- Timeout: 5s por requisição, 1 tentativa (sem retry)

### Notion API
- Blocos criados via `notion.historico.criar_relatorio_no_historico`
- Estrutura: página por relatório com seções em tabela

### Slack Bolt
- Handler de menções `@agente`
- Relatórios postados como thread (título na mensagem principal, blocos na thread)

---

## 🧪 Testando os builders

```bash
source venv/bin/activate

# Semanal
python3 -c "
from relatorio.builder_semanal import gerar_relatorio_semanal
b = gerar_relatorio_semanal('source_of', publicar=False)
print(f'semanal OK — {len(b)} blocos')
"

# Mensal
python3 -c "
from relatorio.builder_mensal import gerar_relatorio_mensal
b = gerar_relatorio_mensal('source_of', publicar=False)
print(f'mensal OK — {len(b)} blocos')
"

# Diário
python3 -c "
from relatorio.builder_diario import gerar_relatorio_diario
b = gerar_relatorio_diario('source_of', publicar=False)
print(f'diario OK — {len(b)} blocos')
"
```

---

## 📝 Regras de Negócio

Definidas em `relatorio/regras.py`:

- **ROAS mínimo aceitável:** 1.5×
- **Dias sem conversão:** alerta após 3 dias
- **Fator de imposto Meta:** 1.1215 (aplicado sobre investimento)
- **ROAS real:** `Receita Shopify ÷ (Investimento Meta × 1,1215)`
- **Janela de atribuição:** configurável por cliente

---

## 📁 Histórico de alterações

Cada deploy relevante gera um arquivo em `alteracoes/YYYY-MM-DD_<slug>.md` com:
- Commit hash e mensagem
- Arquivos alterados
- Descrição do que mudou
- Tabela de impacto antes/depois

---

## ⚠️ Avisos do Validador

O validador (`db/validator.py`) emite warnings esperados:

| Warning | Causa | Ação |
|---|---|---|
| `invest_zero_com_conversao` | Atribuição cross-campaign no Meta | Esperado, ignorar |
| `roas_ausente_com_receita` | Meta não reportou ROAS para a linha | Esperado, ignorar |
| `consistencia_roas` | Janela de atribuição diferente no Meta | Esperado, ignorar |

---

## 🔒 Segurança

- `.env` **nunca** vai ao repositório (`.gitignore` configurado)
- Tokens de API armazenados apenas em variáveis de ambiente
- Banco SQLite local — sem exposição externa
- Servidor exposto apenas via proxy reverso (Apache/nginx)

---

## 📜 Licença

Projeto proprietário — Guará Marketing. Todos os direitos reservados.
