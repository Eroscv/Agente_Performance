# ⚙️ Agente de Performance Guará

> Analista de mídia paga com IA — Meta Ads + Google Ads — para os clientes da **Guará Marketing**.

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-2.0-green.svg)](https://fastapi.tiangolo.com)
[![Claude](https://img.shields.io/badge/Claude-Haiku_4.5-orange.svg)](https://anthropic.com)
[![Slack](https://img.shields.io/badge/Slack-Bolt-blueviolet.svg)](https://slack.dev/bolt-python)
[![Notion](https://img.shields.io/badge/Notion-API-black.svg)](https://developers.notion.com)

---

## 📌 O que é

O **Agente de Performance Guará** é um sistema de análise de campanhas publicitárias acionado por linguagem natural via Slack. Ele processa dados de **Meta Ads** (Source OF) e **Google Ads** (POD Kombucha), responde perguntas em português com métricas calculadas pelas **regras de negócio da Guará**, e publica relatórios automáticos no Notion.

### Capacidades principais

| Funcionalidade | Descrição |
|---|---|
| 💬 **NLP via Claude** | Interpreta perguntas em linguagem natural, extrai intenções, datas e campanhas |
| 📊 **Análise de campanhas** | ROAS, CPA, CTR, CPM, funil, compras, receita, alcance, frequência |
| 🔄 **Comparativos** | WoW, MoM, antes/depois de data, dois períodos, ranking por qualquer métrica |
| 📝 **Relatórios Notion** | Páginas automáticas com tabelas, métricas e alertas, publicadas toda segunda-feira |
| ⚡ **Slash commands Slack** | `/hoje`, `/semana`, `/top`, `/alertas`, `/relatorio`, `/ajuda` |
| 🤖 **Multi-cliente** | Source OF (Meta Ads) + POD Kombucha (Google Ads) com roteamento automático |
| 🗄️ **Cache em memória** | Respostas recentes retornadas instantaneamente (TTL configurável) |

---

## 🏗️ Arquitetura do Sistema

```
┌──────────────────────────────────────────────────────────────┐
│                         SLACK                                │
│  @agente / DM / /slash-command                               │
└────────────────────────┬─────────────────────────────────────┘
                         │ POST /slack/events
┌────────────────────────▼─────────────────────────────────────┐
│                    main.py — FastAPI                         │
│     (porta 8001, exposto via Apache HTTPS 44.204.251.251)    │
└────────┬───────────────────────────────┬─────────────────────┘
         │                               │
┌────────▼──────────┐        ┌───────────▼──────────────────┐
│ slack/handlers.py │        │        api/app.py            │
│  Bolt AsyncApp    │        │  24 endpoints REST (debug)   │
└────────┬──────────┘        └───────────────────────────────┘
         │
┌────────▼──────────────────────────────────────────┐
│                 agent/engine.py                   │
│  ① cache → ② NLP → ③ fuzzy match → ④ queries    │
│  ⑤ comparativos → ⑥ delta → ⑦ formata resposta  │
└──────┬───────────┬──────────────────┬─────────────┘
       │           │                  │
┌──────▼──┐  ┌─────▼────┐   ┌────────▼──────────────┐
│agent/   │  │agent/    │   │  agent/comparatives   │
│nlp.py   │  │queries.py│   │  WoW/MoM/periodos/rank│
│(Claude) │  │(SQLite)  │   └───────────────────────┘
└──────┬──┘  └─────┬────┘
       │           │
┌──────▼───────────▼─────────────────────────────────┐
│              db/ (SQLite)                          │
│  campanhas.db — Meta Ads Source OF                 │
│  pod_gads_diario — Google Ads POD Kombucha         │
│  cache.db · context.db · logs.db                  │
└───────────────────────────┬────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────┐
│           agent/claude_client.py                   │
│  Formata resposta final com Claude Haiku 4.5       │
└───────────────────────────┬────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────┐
│              notion/ (relatórios)                  │
│  client.py · historico.py · relatorio.py           │
│  → Publica páginas no Notion dos clientes          │
└────────────────────────────────────────────────────┘
```

### Fluxo de uma pergunta no Slack

```
Usuário: "@agente qual campanha teve melhor ROAS em junho?"
    │
    ▼
slack/handlers.py — ACK imediato (< 3s Slack timeout)
    │
    ▼
agent/engine.py
    ├─ 1. cache.get("qual campanha...") → miss
    ├─ 2. nlp.interpretar() → intent=top_roas, data_inicio=2026-06-01, data_fim=2026-06-30
    ├─ 3. fuzzy_match_campaign() → sem campanha específica
    ├─ 4. queries.top_campanhas_roas(10, "2026-06-01", "2026-06-30")
    ├─ 5. dados_extra → {} (single intent)
    └─ 6. cache.set(...)
    │
    ▼
claude_client.gerar_resposta() → texto formatado em Markdown Slack
    │
    ▼
Slack: resposta no thread com dados + link Notion (se `/relatorio`)
```

---

## 💬 Como Utilizar

### Via Slack — Linguagem Natural

Mencione `@agente` em qualquer canal ou mande uma DM diretamente:

```
@agente como foi o mês de junho?
@agente qual campanha teve melhor ROAS essa semana?
@agente compare a ABO vs Advantage em maio
```

### Via Slack — Slash Commands

| Comando | Exemplo | O que faz |
|---|---|---|
| `/hoje` | `/hoje` | Resumo do dia atual |
| `/semana` | `/semana` | Últimos 7 dias |
| `/mes` | `/mes` · `/mes junho` | Mês atual ou específico |
| `/top` | `/top` · `/top compras` · `/top 10` | Top campanhas por ROAS ou compras |
| `/campanha` | `/campanha ABO` | Detalhe de uma campanha |
| `/campanhas` | `/campanhas` | Lista todas as campanhas |
| `/compare` | `/compare ABO vs Advantage` | Comparação lado a lado |
| `/funil` | `/funil` · `/funil ABO` | Funil de conversão geral ou de uma campanha |
| `/alertas` | `/alertas` | Campanhas com ROAS baixo ou sem conversão |
| `/relatorio` | `/relatorio` · `/relatorio mes` · `/relatorio notion` | Relatório no Notion |
| `/exemplos` | `/exemplos` | Mais exemplos de perguntas |
| `/metricas` | `/metricas` | Lista de todas as métricas disponíveis |
| `/status` | `/status` | Status do sistema |
| `/ajuda` | `/ajuda` | Menu de ajuda completo |

### Exemplos Práticos

**Análise geral:**
```
@agente resumo completo de junho — investimento, receita e compras
@agente como estão as campanhas esse mês?
@agente visão geral dos últimos 30 dias
```

**Rankings:**
```
@agente qual campanha teve melhor ROAS essa semana?
@agente top 3 campanhas que geraram mais compras em 2026
@agente qual teve mais cliques no link em junho?
```

**Campanha específica:**
```
@agente como foi a ABO nos últimos 30 dias?
@agente mostre o funil de conversão da Advantage em junho
@agente como evoluiu o investimento da ABO dia a dia em junho?
```

**Comparativos:**
```
@agente compare a Advantage vs ABO em maio
@agente como foi semana a semana em junho?
@agente compare junho com maio
@agente performance antes e depois de 15/06
```

**Alertas e problemas:**
```
@agente qual campanha está com ROAS abaixo de 1?
@agente tem alguma campanha gastando sem converter?
@agente alguma campanha com frequência muito alta?
```

**POD Kombucha (Google Ads):**
```
@agente POD como foi a semana passada?
@agente POD Kombucha resumo de junho
@agente resultados do POD nos últimos 30 dias
```

**Relatórios e exportação:**
```
/relatorio          → relatório da semana passada no Notion
/relatorio mes      → relatório do mês passado no Notion
@agente gera um excel de junho
@agente salva no Notion o resumo de maio
```

### Períodos que o Agente Entende

| Expressão | Interpretação |
|---|---|
| `hoje` | Data atual |
| `ontem` | Dia anterior |
| `essa semana` / `semana atual` | Segunda → domingo da semana corrente |
| `semana passada` | Segunda → domingo da semana anterior |
| `esse mês` / `mês atual` | Do dia 1 ao dia atual do mês corrente |
| `mês passado` | Mês calendário completo anterior |
| `últimos 7 dias` / `30 dias` / `90 dias` | Período contado a partir de hoje |
| `em junho` / `junho de 2026` | Mês completo |
| `janeiro a março` | Período entre dois meses |
| `Q1` / `Q2` / `segundo trimestre` | Trimestre completo |
| `01/06/2026` | Data específica (formato BR) |
| `2026-06-01` | Data específica (formato ISO) |
| `black friday` | Sinônimo configurado |

### Como Funciona o Roteamento de Clientes

O agente detecta automaticamente para qual cliente a pergunta se refere:

- **Source OF (Meta Ads)** → padrão quando nenhum cliente é mencionado
- **POD Kombucha (Google Ads)** → qualquer pergunta com `POD`, `Kombucha`, `Google Ads do POD`
- **Consórcio Sorteado** → menção a `consórcio`, `sorteado`
- **Máximo Serviço** → menção a `máximo`, `serviço`
- **BE MUSHIN** → menção a `mushin`
- **Bazar do Precatório** → menção a `bazar`, `precatório`

### Dica: Reação 👍

Se uma resposta do agente foi especialmente boa, dê 👍 nela no Slack. A resposta é salva na **Base de Conhecimento do Notion** e marcada como aprovada — o agente aprende com o que funciona.

---

## 📐 Regras de Negócio

> Definidas em reunião com Bianca e Eros (Guará Marketing). **Imutáveis sem aprovação.**

| Métrica | Fórmula | Observação |
|---|---|---|
| **Investimento ajustado** | `spend × 1,1215` | Fator de imposto fixo |
| **ROAS** | `Receita Shopify ÷ Investimento ajustado` | **Nunca usar o ROAS do Meta** |
| **CPA** | `Investimento ajustado ÷ Nº de compras` | **Nunca usar o CPA do Meta** |
| **CAC** | `Investimento ajustado ÷ Novos clientes (Shopify via UTM)` | — |
| **CPC** | Direto da plataforma Meta/Google | **Não recalcular** |
| **Ticket Médio** | `Receita ÷ Transações` (Shopify) | — |

> ⚠️ Fator de imposto: **1,1215** (1,1225 foi descartado).

---

## 🗃️ Estrutura do Projeto

```
agente-performance/
├── main.py                     # Entrypoint FastAPI + Slack Bolt
├── clientes.json               # Registry de clientes (sem código)
├── .env                        # Variáveis de ambiente (não commitado)
├── .env.example                # Template de variáveis
│
├── agent/                      # Núcleo de inteligência
│   ├── engine.py               # Orquestrador: NLP → queries → cache → resposta
│   ├── nlp.py                  # NLP via Claude: extrai intent, campanha, datas
│   ├── comparatives.py         # Comparativos WoW/MoM/períodos/ranking
│   ├── queries.py              # 20+ funções SQL prontas
│   ├── claude_client.py        # Formata resposta final com Claude
│   ├── context_store.py        # Memória de thread (SQLite, TTL 24h)
│   ├── conversation_log.py     # Log estruturado de conversas
│   ├── cache.py                # Cache em memória com TTL
│   └── synonyms.py             # Sinônimos de períodos e métricas
│
├── api/
│   └── app.py                  # 24 endpoints REST (FastAPI)
│
├── db/                         # Camada de banco de dados
│   ├── connection.py           # Singleton SQLite com row_factory
│   ├── schema.py               # COLUMN_MAP: 94 colunas PT-BR → SQL
│   ├── loader.py               # Carga CSV → SQLite (idempotente)
│   ├── loader_sheets.py        # Carga Google Sheets → SQLite (Source OF)
│   ├── loader_pod.py           # Carga Google Sheets → SQLite (POD Kombucha)
│   ├── loader_pod.py           # Carga Google Sheets → SQLite (POD Kombucha)
│   ├── registry.py             # Roteamento dinâmico por cliente
│   ├── validator.py            # Validação de dados na carga
│   └── cron_log.py             # Log de execuções do cron
│
├── notion/                     # Integração Notion
│   ├── client.py               # Cliente HTTP Notion API
│   ├── relatorio.py            # Gerador de páginas/blocos Notion
│   ├── historico.py            # Publicação de histórico de campanhas
│   └── knowledge_base.py       # Base de conhecimento no Notion
│
├── slack/                      # Integração Slack
│   ├── handlers.py             # Handlers Bolt (menções, DMs, slash commands)
│   ├── formatter.py            # Markdown → Slack blocks
│   └── poster.py               # Postagem assíncrona no Slack
│
├── reports/
│   └── exporter.py             # Export para Excel/PDF
│
├── scripts/
│   ├── gerar_relatorio.py      # CLI: gera relatório manual
│   ├── cron_relatorio.py       # Cron: relatório semanal/mensal Source OF
│   ├── cron_relatorio_pod.py   # Cron: relatório semanal/mensal POD Kombucha
│   ├── cron_todos_relatorios.py # Cron: todos os clientes de uma vez
│   ├── gerar_readme_consultas.py # Gera CONSULTAS.md a partir dos logs
│   └── push_consultas.sh       # Commit + push CONSULTAS.md (cron diário)
│
└── data/
    ├── campanhas.db            # SQLite Source OF (Meta Ads)
    ├── cache.db                # Cache de respostas
    ├── context.db              # Histórico de threads Slack
    └── logs.db                 # Log de conversas
```

---

## 🔌 Clientes Suportados

Configurados em `clientes.json` — adicionar um novo cliente não exige mudanças no código.

| Cliente | Plataforma | Tabela SQLite | Cron (Relat.) | Slack Emoji |
|---|---|---|---|---|
| **Source OF** | Meta Ads | `campanhas` | Seg 8h · Dia 1 8h | 🔵 |
| **POD Kombucha** | Google Ads | `pod_gads_diario` | Seg 8h · Dia 1 8h | 🟢 |
| Consórcio Sorteado | Meta Ads | `campanhas` | — | — |
| Máximo Serviço | Meta Ads | `campanhas` | — | — |
| BE MUSHIN | Meta Ads | `campanhas` | — | — |
| Bazar do Precatório | Meta Ads | `campanhas` | — | — |

---

## 🤖 Intenções Reconhecidas (NLP)

O motor NLP (`agent/nlp.py`) extrai automaticamente:

| Intent | Trigger (exemplos) | Query executada |
|---|---|---|
| `top_roas` | "melhor ROAS", "mais eficiente" | `top_campanhas_roas()` |
| `top_compras` | "mais compras", "mais vendas" | `top_campanhas_compras()` |
| `top_receita` | "mais receita", "mais faturou" | `top_campanhas_receita()` |
| `top_ctr` | "melhor CTR", "taxa de clique" | `top_campanhas_ctr()` |
| `top_cpm` | "mais barato por impressão" | `top_campanhas_cpm()` |
| `top_alcance` | "mais alcance", "mais pessoas" | `top_campanhas_alcance()` |
| `top_cliques` | "mais cliques no link" | `top_campanhas_cliques()` |
| `alertas_roas` | "ROAS baixo", "campanhas ruins" | `alertas_roas_baixo()` |
| `sem_conversao` | "sem compra", "gastando sem retorno" | `campanhas_sem_conversao()` |
| `funil` | "funil", "carrinho", "checkout" | `metricas_funil()` |
| `funil_detalhado` | "onde vaza", "custo por etapa" | `custo_funil_detalhado()` |
| `evolucao_diaria` | "evolução dia a dia", "histórico" | `evolucao_diaria()` |
| `historico_mensal` | "mês a mês", "sazonalidade" | `historico_mensal()` |
| `comparar` | "compare", "vs", "lado a lado" | `comparar_campanhas_detalhado()` |
| `comparar_periodos` | "junho vs maio" | `comparar_periodos()` |
| `comparar_wow` | "semana a semana", "WoW" | `comparar_semana_a_semana()` |
| `comparar_mom` | "mês a mês", "MoM" | `comparar_mes_a_mes()` |
| `comparar_antes_depois` | "antes e depois de", "desde" | `comparar_antes_depois()` |
| `ranking_metrica` | "ranking de", "ordena por" | `ranking_comparativo()` |
| `saturacao_frequencia` | "frequência alta", "saturando" | `saturacao_frequencia()` |
| `metricas_video` | "vídeo", "ThruPlay", "retenção" | `metricas_video()` |
| `metricas_engajamento` | "engajamento", "curtidas" | `metricas_engajamento()` |
| `metricas_mensageria` | "mensagem", "WhatsApp", "lead" | `metricas_mensageria()` |
| `resumo_executivo` | "dashboard", "panorama geral" | `resumo_executivo()` |
| `performance_campanha` | "performance da [campanha]" | `performance_campanha()` |
| `resumo_geral` | *fallback* | `resumo_por_campanha()` |

### Períodos reconhecidos automaticamente

- Datas ISO (`2026-06-01`) e BR (`01/06/2026`)
- `hoje`, `ontem`, `essa semana`, `semana passada`, `semana atual`
- `este mês`, `mês passado`, `últimos 7/30/90 dias`
- `janeiro`, `fevereiro`, ..., `dezembro` (+ ano opcional)
- Dois períodos para comparativo: *"compare junho com maio"*

---

## ⚙️ Setup

### Pré-requisitos

- Python 3.12+
- Conta Slack com app configurado (Bolt)
- API Key Anthropic (Claude Haiku 4.5)
- API Key Notion + integração com os databases dos clientes
- Apache/Nginx com HTTPS (proxy reverso para porta 8001)

### Instalação

```bash
# Clonar e entrar no projeto
cd agente-performance

# Criar ambiente virtual
python3.12 -m venv venv
source venv/bin/activate

# Instalar dependências
pip install -r requirements.txt

# Configurar variáveis
cp .env.example .env
# Editar .env com as chaves reais

# Carregar dados iniciais
python -m db.loader           # CSV → SQLite (Source OF)
python -m db.loader_sheets    # Google Sheets → SQLite (Source OF)
python -m db.loader_pod       # Google Sheets → SQLite (POD Kombucha)
```

### Rodar em desenvolvimento

```bash
source venv/bin/activate
python main.py
# → http://localhost:8001
# → Docs: http://localhost:8001/docs
```

### Serviço systemd (produção)

O agente roda como serviço `agente-guara.service` na porta **8001**.

```bash
# Status
systemctl status agente-guara.service

# Reiniciar após mudanças
systemctl restart agente-guara.service

# Ver logs em tempo real
journalctl -u agente-guara.service -f
```

### Apache (proxy reverso)

O serviço é exposto em `https://44.204.251.251/guara/` via Apache.

Rotas configuradas:
- `POST /guara/slack/events` → Slack Bolt
- `GET /guara/health` → health check

---

## 🔧 Variáveis de Ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | API Key Claude (Anthropic) |
| `SLACK_BOT_TOKEN` | ✅ | Token `xoxb-...` do bot Slack |
| `SLACK_SIGNING_SECRET` | ✅ | Signing secret do app Slack |
| `NOTION_API_KEY` | ✅ | Token de integração Notion |
| `PORT` | — | Porta do servidor (padrão: 8001) |
| `CLAUDE_MODEL` | — | Modelo Claude (padrão: `claude-haiku-4-5-20251001`) |
| `CACHE_TTL_SECONDS` | — | TTL do cache de respostas (padrão: 3600) |
| `ROAS_ALERT_THRESHOLD` | — | ROAS mínimo para alertas (padrão: 1.5) |
| `NO_CONVERSION_DAYS` | — | Dias sem conversão para alerta (padrão: 3) |
| `LOG_LEVEL` | — | Nível de log (`INFO`, `DEBUG`, padrão: `INFO`) |
| `SHOPIFY_STORE` | — | Subdomínio da loja Shopify |
| `SHOPIFY_TOKEN` | — | Token de acesso Shopify Admin API |

---

## 📡 API REST

O agente expõe uma API REST para debug e integração externa.

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/health` | Status do sistema e banco |
| `GET` | `/campanhas` | Lista todas as campanhas com totais |
| `POST` | `/consulta` | Consulta em linguagem natural |
| `POST` | `/sql` | SQL livre (apenas SELECT) |
| `GET` | `/resumo` | Resumo geral (filtros `?data_inicio=&data_fim=`) |
| `GET` | `/top/roas` | Top campanhas por ROAS |
| `GET` | `/top/compras` | Top campanhas por compras |
| `GET` | `/campanha/{nome}` | Série temporal de uma campanha |
| `GET` | `/campanha/{nome}/funil` | Métricas de funil |
| `GET` | `/alertas/roas` | Campanhas com ROAS abaixo do limite |
| `GET` | `/evolucao` | Evolução diária geral ou por campanha |
| `POST` | `/db/reload` | Recarrega CSV no banco (operação idempotente) |

Documentação interativa: `http://localhost:8001/docs`

---

## 📊 Databases Notion

| Cliente | Database ID | Tipo |
|---|---|---|
| Source OF | `37a8a727-a8ff-81ba-aecd-e3ffff1f7f7e` | Relatórios |
| Consórcio Sorteado | `37a8a727-a8ff-814c-a1dc-e5e0b9f5993a` | Relatórios |
| Máximo Serviço | *(resolvido na primeira chamada)* | Relatórios |
| BE MUSHIN | *(resolvido na primeira chamada)* | Relatórios |
| Bazar do Precatório | *(resolvido na primeira chamada)* | Relatórios |
| POD Kombucha | *(resolvido na primeira chamada)* | Relatórios |

---

## 📄 Subagente de Relatoria

O **Subagente de Relatoria** é o pipeline responsável por gerar e publicar relatórios formatados de campanhas. Opera de forma **autônoma** (via cron) ou **sob demanda** (via Slack ou CLI), para todos os clientes cadastrados.

### Arquitetura do Pipeline

```
Disparo (cron / Slack /relatorio / CLI)
    │
    ▼
scripts/cron_todos_relatorios.py
    ├─ 1. db.registry.get_loader()   → db/loader_sheets.py ou db/loader_pod.py
    │      └─ Atualiza SQLite com dados frescos da planilha Google Sheets
    ├─ 2. verificar_alertas()         → checa ROAS abaixo do threshold
    │      └─ Posta alerta âncora + thread detalhada no Slack se houver
    ├─ 3. db.registry.get_builder()  → relatorio/builder.py (Meta) ou builder_pod.py (Google)
    │      ├─ _buscar_campanhas()         → agrega campanhas do SQLite no período
    │      ├─ Shopify data (metricas_site) → receita real, novos clientes, CAC, recompra
    │      ├─ _blocos_*()                 → monta blocos Notion (tabelas, KPIs, funil, etc.)
    │      └─ notion/historico.py         → publica página no Notion
    ├─ 4. _calcular_delta()           → variação vs período anterior (ROAS, Invest.)
    └─ 5. slack/poster.py             → posta âncora + thread com resumo no Slack
```

### Estrutura do Relatório Notion Gerado

#### Semanal (Source OF — Meta Ads)
```
📊 Source OF · Meta Ads Semanal · 14/07/2026 → 20/07/2026
├─ ⚙️ Callout: gerado automaticamente pelo Agente de Performance
├─ 💰 Resumo Geral da Conta
│   ├─ Investimento bruto + ajustado (×1,1215)
│   ├─ Receita Shopify real + ROAS Real
│   ├─ Impressões | Cliques | CTR | CPM | CPC | Frequência
│   └─ Compras | CPA | Funil (Carrinho → Checkout → Compra)
├─ 📊 Métricas de Site (Shopify)
│   └─ Ticket Médio | Novos Clientes | CAC | % Desconto | Pedidos >R$300 | Taxa de Recompra
├─ 📊 Tabela de Campanhas
│   └─ Campanha | Invest. | Invest.+Imposto | Compras | CPA | ROAS | CPC | CTR
├─ 💰 Alocação de Budget
│   └─ % do orçamento por campanha (ordem decrescente)
├─ 🔄 Comparativo com Período Anterior
│   └─ Tabela: métrica | atual | anterior | variação %
├─ 📈 Funil de Conversão
│   └─ Alcance → Impressões → Cliques → Carrinho → Checkout → Compras
└─ 🚨 Alertas Automáticos
    └─ Campanhas com ROAS < 1.5 ou sem conversão
```

#### Mensal (Source OF)
Têm as mesmas seções + adicionais:
```
├─ 📅 Visão Mensal Diária (receita + investimento dia a dia)
├─ 🔍 Análise das Últimas 2 Semanas (comparação semana a semana)
└─ 📋 Histórico de Compras por SKU (dados Shopify)
```

### Como Usar

#### Via Slack (mais comum)
```
/relatorio             → relatório da semana passada (Source OF)
/relatorio mes         → relatório do mês passado (Source OF)
/relatorio notion      → só publica no Notion (sem resumo no Slack)
@agente gera relatório semanal   → mesmo que /relatorio
@agente relatório de junho       → mês específico
@agente gera excel e notion      → publica no Notion + envia .xlsx no Slack
```

#### Via CLI
```bash
# Relatório semanal — Source OF (semana passada)
python scripts/cron_relatorio.py --periodo semana

# Relatório mensal — Source OF
python scripts/cron_relatorio.py --periodo mes

# Todos os clientes ativos — semanal
python scripts/cron_todos_relatorios.py --periodo semana

# Todos os clientes — mensal
python scripts/cron_todos_relatorios.py --periodo mes

# Cliente específico
python scripts/cron_todos_relatorios.py --cliente source_of --periodo semana
python scripts/cron_todos_relatorios.py --cliente pod_kombucha --periodo semana

# Só alertas (sem gerar relatório completo)
python scripts/cron_todos_relatorios.py --apenas-alertas

# Só sincroniza banco (sem relatório)
python scripts/cron_todos_relatorios.py --apenas-loader

# Sem publicar no Notion (só Slack)
python scripts/cron_todos_relatorios.py --no-notion --periodo semana

# Período customizado
python scripts/gerar_relatorio.py --cliente source_of \
  --inicio 2026-07-01 --fim 2026-07-14
```

#### Via Python (importar diretamente)
```python
from relatorio.builder import gerar_relatorio_semanal, gerar_relatorio_mensal

# Semanal automático (semana passada)
resultado = gerar_relatorio_semanal(cliente="source_of")
print(resultado["url"])  # link da página Notion

# Mensal (mês passado)
resultado = gerar_relatorio_mensal(cliente="source_of")

# Período customizado
from datetime import date
resultado = gerar_relatorio_semanal(
    cliente="source_of",
    data_inicio=date(2026, 7, 1),
    data_fim=date(2026, 7, 14),
)

# POD Kombucha
from relatorio.builder_pod import gerar_relatorio_semanal_pod
resultado = gerar_relatorio_semanal_pod()
```

### Agendamento Automático (Cron)

| Tipo | Schedule | Clientes | O que faz |
|---|---|---|---|
| Semanal | **Seg 8h UTC** | Todos ativos | Gera relatório da semana passada |
| Mensal | **Dia 1 às 8h UTC** | Todos ativos | Gera relatório do mês passado |
| Shopify Sync | **Todo dia 6h UTC** | Source OF | Sincroniza receita dos últimos 8 dias |

Quando o cron dispara, o fluxo completo é:
1. Loader sincroniza dados frescos da planilha Google Sheets → SQLite
2. Alertas proativos são verificados e postados no Slack se houver
3. Relatório completo é gerado e publicado no Notion
4. Resumo é postado no canal `#performance` com link do Notion

### Alertas Proativos

Antes de gerar o relatório completo, o sistema verifica automaticamente:

| Threshold | Plataforma | Comportamento |
|---|---|---|
| ROAS < 2.0 | Meta Ads (Source OF) | Posta alerta âncora + thread com tabela de campanhas problemáticas |
| ROAS < 1.5 | Google Ads (POD Kombucha) | Posta alerta âncora + thread com tabela |

Variaveis de ambiente para ajustar:
```bash
ALERTA_ROAS_MIN=2.0       # threshold Meta Ads
ALERTA_ROAS_MIN_GADS=1.5  # threshold Google Ads
```

### O que o Subagente Retorna

Toda chamada ao builder retorna um `dict` com:
```python
{
    "url":          "https://notion.so/...",  # link da página publicada
    "page_id":      "uuid",
    "resumo": {
        "invest":         float,   # investimento total Meta
        "invest_ajustado": float,  # com fator 1,1215
        "receita":        float,   # receita Meta (estimada)
        "receita_shopify": float,  # receita Shopify real
        "roas":           float,   # ROAS Meta
        "roas_real":      float,   # ROAS Shopify/Meta ajustado
        "compras":        int,
        "cpa":            float,
        "cac":            float,   # Invest. ajustado / Novos clientes
        ...
    },
    "num_campanhas": int,
    "alertas":      ["campanha X: ROAS 0.8x", ...],
}
```

### Adicionar Novo Cliente

Adicionar um cliente novo ao pipeline de relatoria **não requer alterar código**:

1. Adicionar bloco em `clientes.json`
2. Criar database no Notion e compartilhar com a integração
3. Configurar `notion_key` para resolver o ID correto

```json
"novo_cliente": {
  "nome":            "Novo Cliente",
  "ativo":           true,
  "plataforma":      "meta_ads",
  "loader":          "db.loader_sheets",
  "loader_fn":       "carregar_sheets",
  "builder":         "relatorio.builder",
  "builder_semanal": "gerar_relatorio_semanal",
  "builder_mensal":  "gerar_relatorio_mensal",
  "notion_key":      "novo_cliente",
  "slack_emoji":     "🟠",
  "slack_label":     "Novo Cliente · Meta Ads",
  "tabela_db":       "campanhas",
  "schedules": {
    "semanal": "0 8 * * 1",
    "mensal":  "0 8 1 * *"
  }
}
```

---

## 🔄 Cron Jobs (Relatórios Automáticos)

| Script | Schedule | Descrição |
|---|---|---|
| `cron_relatorio.py` | Seg 8h + Dia 1 8h | Relatório semanal + mensal Source OF |
| `cron_relatorio_pod.py` | Seg 8h + Dia 1 8h | Relatório semanal + mensal POD Kombucha |
| `cron_todos_relatorios.py` | Seg 8h + Dia 1 8h | Todos os clientes em sequência |
| `gerar_readme_consultas.py` | Todo dia 00:00 | Atualiza CONSULTAS.md com os logs |
| `push_consultas.sh` | Todo dia 00:00 | Commit + push CONSULTAS.md |

Rodar manualmente:
```bash
# Relatório semanal Source OF
python scripts/gerar_relatorio.py --cliente source_of --periodo semana

# Todos os clientes
python scripts/cron_todos_relatorios.py
```

---

## 🧪 Testes

```bash
# Testes completos pós-melhorias
python scripts/test_suite_post_melhorias.py

# Testes de consultas
pytest # (pytest configurado no projeto)
```

---

## 🗺️ Melhorias Planejadas / Roadmap

### 🔴 Alta Prioridade

**1. Integração Shopify em tempo real**
Atualmente os dados de receita vêm do CSV do Meta. Integrar a API Shopify Admin para buscar receita real por UTM (`utm_campaign`) eliminaria a dependência de exports manuais e tornaria o ROAS mais preciso.
```
Esforço: Alto | Impacto: Crítico
```

**2. Dashboard web interativo**
Criar uma SPA (React/Next.js ou Streamlit) que consuma a API REST existente e exiba gráficos de linha, barras e tabelas ordenáveis. Eliminaria a dependência do Slack para visualização.
```
Esforço: Alto | Impacto: Alto
```

**3. Upload automático de CSV via Slack**
Permitir que o usuário arraste o export do Meta para um canal Slack e o bot processe automaticamente (`/db reload`), eliminando a etapa manual de upload ao servidor.
```
Esforço: Médio | Impacto: Alto
```

### 🟡 Média Prioridade

**4. Suporte a mais clientes/plataformas**
Adicionar loaders para TikTok Ads, LinkedIn Ads e Google Analytics. O `clientes.json` já suporta múltiplas plataformas; falta implementar os loaders específicos.
```
Esforço: Médio | Impacto: Médio
```

**5. Alertas proativos via Slack**
Em vez de esperar pergunta, o agente verificaria diariamente: campanhas com ROAS abaixo do threshold, campanhas sem conversão há N dias, budget alto com CTR muito baixo. Enviaria alertas automáticos no canal `#performance`.
```
Esforço: Baixo | Impacto: Alto
```

**6. Cache distribuído (Redis)**
O cache atual é in-memory. Com Redis, o cache sobreviveria a restarts e poderia ser compartilhado entre múltiplas instâncias. Útil se o volume de consultas crescer.
```
Esforço: Baixo | Impacto: Médio
```

**7. Autenticação nos endpoints REST**
A API REST atual não tem autenticação. Adicionar API Key ou JWT para evitar acesso não autorizado.
```
Esforço: Baixo | Impacto: Médio
```

**8. Análise de criativos (imagens/vídeos)**
Usar Claude Vision para analisar os criativos das campanhas e correlacionar estilo visual com performance (hook rate, CTR, retenção de vídeo). Dado enriquecido viria de `ad_name` + assets do Meta.
```
Esforço: Alto | Impacto: Alto
```

### 🟢 Baixa Prioridade / Futuros

**9. Multi-idioma**
Suporte a perguntas em inglês (atualmente só português). O NLP do Claude já suporta; faltaria adaptar o system prompt e os sinônimos.

**10. Webhooks outbound**
Ao publicar um relatório no Notion, disparar webhook para outros sistemas (ex: Zapier, Pipedrive, painel do cliente).

**11. Comparativo entre clientes**
Visão consolidada de todos os clientes em uma única consulta: "qual cliente tem o melhor ROAS médio esse mês?".

**12. Histórico de ROAS com anomalia detection**
Usar Z-score ou IQR para detectar automaticamente quando uma campanha está com performance anormal (muito acima ou abaixo da média histórica dela mesma).

**13. Integração com Windsor.ai / API Meta oficial**
Em vez de CSV manual, buscar dados diretamente via Windsor.ai ou Graph API do Meta para atualização em tempo quase-real.

---

## 🏛️ Infraestrutura

| Componente | Detalhe |
|---|---|
| Servidor | AWS EC2 Ubuntu — IP `44.204.251.251` |
| Proxy | Apache 2.4 — HTTPS (cert snakeoil) |
| Serviço | `agente-guara.service` — systemd, User=ubuntu |
| Porta interna | `8001` |
| URL externa | `https://44.204.251.251/guara/` |
| Banco | SQLite (`data/campanhas.db`) |
| Runtime | Python 3.12 + venv |
| Modelo IA | Claude Haiku 4.5 (NLP + resposta) |

---

## 🔒 Segurança

- Dados de campanhas ficam **apenas no servidor** (SQLite local, sem cloud storage)
- API REST sem autenticação — acessível apenas internamente (localhost / Apache)
- Tokens e chaves em `.env` (não commitado — ver `.gitignore`)
- Apenas SELECTs são permitidos via `/sql` (validação no `consultar_sql()`)
- Dados de clientes não são expostos em logs públicos

---

## 📚 Documentação Adicional

- [`MVP.md`](./MVP.md) — histórico de decisões e etapas do MVP
- [`CONSULTAS.md`](./CONSULTAS.md) — exemplos reais de consultas dos logs
- [`README_TESTES_CONSULTAS.md`](./README_TESTES_CONSULTAS.md) — testes de consultas
- [`README_TESTES_MELHORIAS.md`](./README_TESTES_MELHORIAS.md) — testes pós-melhorias

---

*Guará Marketing — uso interno · Agente de Performance v2.0*
