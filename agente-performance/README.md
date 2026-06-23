# Agente de Performance Guará

> Assistente conversacional de mídia paga para análise de campanhas **Source OF** no Meta Ads via Slack, com integração ao Notion e exportação de relatórios.

---

## Visão Geral

O Agente de Performance Guará é um sistema de consulta inteligente que permite à equipe da Guará Marketing analisar dados de campanhas Meta Ads da **Source OF** em linguagem natural, diretamente pelo Slack. O agente interpreta perguntas em português, consulta o banco de dados local e gera respostas formatadas com insights acionáveis usando o modelo Claude da Anthropic.

### Capacidades principais

- **Consultas em linguagem natural** — pergunte como se estivesse conversando com um analista
- **10 slash commands** — atalhos rápidos para as consultas mais comuns
- **Exportação de relatórios** — Excel (.xlsx), PDF e páginas no Notion
- **Memória de contexto** — lembra do histórico de cada conversa (thread)
- **Cache inteligente** — evita chamadas repetidas ao banco e à API
- **Log de conversas** — registra todas as interações com latência e cache hit

---

## Dados da Base

| Propriedade | Valor |
|-------------|-------|
| **Cliente** | Source OF |
| **Plataforma** | Meta Ads (Facebook / Instagram) |
| **Registros** | 30.905 |
| **Campanhas únicas** | 32 |
| **Período** | 23/01/2024 → 23/06/2026 |
| **Colunas** | 94 métricas |
| **Banco** | SQLite (`data/campanhas.db`) |
| **Origem** | CSV exportado do Meta Ads / Windsor.ai |

---

## Arquitetura

```
┌──────────────────────────────────────────────────────────┐
│                         SLACK                            │
│   @menção / DM / /comando                                │
└────────────────────┬─────────────────────────────────────┘
                     │ HTTPS → Apache → /guara/slack/events
                     ▼
┌──────────────────────────────────────────────────────────┐
│              slack/handlers.py  (Bolt AsyncApp)          │
│  • ACK imediato (evita timeout 3s)                       │
│  • Detecta formato de exportação (excel/pdf/notion)      │
│  • Chama _processar_e_responder()                        │
│  • Registra log no finally (sempre)                      │
└────────────────────┬─────────────────────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
┌──────────────────┐  ┌─────────────────────┐
│  agent/cache.py  │  │ agent/context_store  │
│  TTL: 1h (3600s) │  │ SQLite thread hist.  │
│  chave: md5(q)   │  │ TTL: 24h / 10 msgs  │
└──────────┬───────┘  └──────────┬──────────┘
           │   cache miss        │ contexto da thread
           ▼                     ▼
┌──────────────────────────────────────────────────────────┐
│                    agent/engine.py                       │
│  1. interpretar() → Claude extrai intents + datas        │
│  2. fuzzy_match_campaign() → tolera variações de nome    │
│  3. _validar_campanha() → erro com sugestões se não acha │
│  4. _executar_intent() → despacha para queries.py        │
│  5. _dados_vazios_sugestao() → orienta se vazio          │
└────────────────────┬─────────────────────────────────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
┌──────────────────┐  ┌─────────────────────┐
│  agent/nlp.py    │  │   agent/queries.py   │
│  Claude → JSON   │  │   10 funções SQL     │
│  intents + datas │  │   SQLite (campanhas) │
│  fallback regex  │  └──────────┬──────────┘
└──────────────────┘             │
                                 ▼
                      ┌──────────────────────┐
                      │  db/connection.py    │
                      │  SQLite singleton    │
                      │  row_factory=dict    │
                      └──────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────┐
│               agent/claude_client.py                     │
│  gerar_resposta() → Claude formata resposta em mrkdwn    │
│  System prompt especializado Source OF / Meta Ads        │
└────────────────────┬─────────────────────────────────────┘
                     │
          ┌──────────┴───────────────────┐
          ▼                              ▼
┌──────────────────────┐   ┌─────────────────────────────┐
│  slack/formatter.py  │   │   Exportação de Relatórios  │
│  markdown → Block Kit│   │                             │
│  header, tabela,     │   │  reports/exporter.py        │
│  section, divider    │   │  → Excel (.xlsx) / PDF      │
└──────────────────────┘   │                             │
                           │  notion/relatorio.py        │
                           │  → Página Notion formatada  │
                           │  → Link retornado no Slack  │
                           └─────────────────────────────┘
```

---

## Estrutura de Diretórios

```
agente-performance/
│
├── main.py                  # Entrypoint: FastAPI + Slack Bolt
├── requirements.txt         # Dependências Python
├── .env                     # Variáveis de ambiente (não versionado)
│
├── agent/                   # Motor de inteligência
│   ├── engine.py            # Orquestrador principal: NLP → SQL → resposta
│   ├── nlp.py               # Interpretação com Claude (intents, datas, campanhas)
│   ├── queries.py           # 10 consultas SQL prontas
│   ├── claude_client.py     # Cliente Anthropic + system prompt Source OF
│   ├── comparatives.py      # Comparações: WoW, MoM, antes/depois, ranking
│   ├── context_store.py     # Memória de thread (SQLite, TTL 24h)
│   ├── cache.py             # Cache em memória com TTL configurável
│   ├── conversation_log.py  # Log estruturado de conversas (data/logs.db)
│   └── synonyms.py          # Mapeamento de sinônimos de métricas
│
├── db/                      # Camada de banco de dados
│   ├── schema.py            # COLUMN_MAP (94 colunas PT-BR → SQL) + CREATE TABLE
│   ├── loader.py            # Carga idempotente CSV → SQLite
│   └── connection.py        # Singleton SQLite com row_factory
│
├── api/                     # API REST (FastAPI)
│   └── app.py               # 24 endpoints
│
├── slack/                   # Integração Slack
│   ├── handlers.py          # Bolt AsyncApp: eventos + 10 slash commands
│   └── formatter.py         # Conversor markdown → Block Kit nativo
│
├── notion/                  # Integração Notion
│   ├── client.py            # Cliente completo: CRUD, queries, helpers
│   └── relatorio.py         # Gerador de páginas formatadas no Notion
│
├── reports/                 # Exportação de arquivos
│   └── exporter.py          # Gerador Excel (.xlsx) e PDF
│
└── data/                    # Dados em tempo de execução
    ├── campanhas.db         # Banco principal (SQLite, ~9MB)
    ├── context.db           # Histórico de threads
    └── logs.db              # Log de conversas
```

---

## Slash Commands

Todos os comandos são efêmeros por padrão (só o solicitante vê a confirmação). A resposta com dados é postada no canal.

| Comando | Argumento | Descrição |
|---------|-----------|-----------|
| `/ajuda` | — | Menu completo de comandos e exemplos |
| `/hoje` | — | Resumo do dia atual |
| `/semana` | — | Resumo dos últimos 7 dias |
| `/mes` | `[mês]` | Mês atual ou `/mes junho` / `/mes 2026-05` |
| `/top` | `[N] [compras]` | Top 5 por ROAS · `/top 10` · `/top compras` |
| `/campanha` | `[nome]` | Performance da campanha · sem arg lista todas |
| `/campanhas` | — | Lista completa com investimento total |
| `/alertas` | — | Campanhas com ROAS baixo ou sem conversão |
| `/funil` | `[campanha]` | Funil de conversão geral ou de uma campanha |
| `/compare` | `A vs B` | Comparação lado a lado de 2 campanhas |
| `/relatorio` | `[formato]` | Exporta relatório do mês: `excel` (padrão) · `pdf` · `notion` · `tudo` |

---

## Consultas em Linguagem Natural

O agente aceita menções (`@nome`) e mensagens diretas (DMs). Exemplos:

### Por período
```
resumo geral de junho
o que aconteceu essa semana?
dados dos últimos 30 dias
compare novembro vs maio
```

### Por campanha
```
como foi a ABO em maio?
performance da Advantage esse mês
tá valendo a pena a campanha do hidratante?
```

### Rankings
```
top 5 campanhas por ROAS em junho
qual campanha gerou mais compras?
quais campanhas são mais eficientes?
```

### Funil e conversão
```
mostre o funil da ABO
qual a taxa de conversão geral?
quantos carrinhos abandonados essa semana?
```

### Alertas
```
alguma campanha com ROAS ruim?
tem campanhas sem conversão?
```

### Exportação
```
gera planilha Excel do resumo de junho
exporta no notion
salvar no Notion para o Source OF
resumo de junho no excel e notion
```

### Períodos reconhecidos automaticamente
```
hoje · ontem · essa semana · semana passada
esse mês · mês passado · últimos 7/30/90 dias
em janeiro · em junho · em 2025
primeiro trimestre · black friday
2026-06-01 · 01/06/2026
```

---

## Intenções Detectadas

| Intent | Descrição | Query executada |
|--------|-----------|----------------|
| `top_roas` | Ranking por ROAS | `top_campanhas_roas()` |
| `top_compras` | Ranking por volume de compras | `top_campanhas_compras()` |
| `performance_campanha` | Série temporal diária | `performance_campanha()` |
| `resumo_geral` | Resumo agregado por campanha | `resumo_por_campanha()` |
| `periodo` | Totais consolidados de um período | `resumo_periodo()` |
| `funil` | Métricas de funil de conversão | `metricas_funil()` |
| `evolucao_diaria` | Evolução dia a dia | `evolucao_diaria()` |
| `alertas_roas` | Campanhas com ROAS problemático | `alertas_roas_baixo()` |
| `listar_campanhas` | Lista todas as campanhas | `listar_campanhas()` |
| `comparar` | Comparação entre campanhas | `comparar_campanhas()` |
| `comparar_periodos` | Período A vs período B | `comparar_periodos()` |
| `comparar_wow` | Semana vs semana anterior | `comparar_semana_a_semana()` |
| `comparar_mom` | Mês vs mês anterior | `comparar_mes_a_mes()` |
| `ranking_metrica` | Ranking por métrica específica | `ranking_comparativo()` |

---

## API REST

Base URL: `http://localhost:8001` (via Apache: `https://44.204.251.251/guara/`)

### Saúde e dados

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/health` | Status do serviço e tamanho do banco |
| `GET` | `/campanhas` | Lista todas as campanhas com métricas |
| `GET` | `/resumo` | Resumo por período (`?data_inicio=&data_fim=`) |
| `GET` | `/top/roas` | Top campanhas por ROAS (`?limit=&data_inicio=`) |
| `GET` | `/top/compras` | Top campanhas por compras |
| `GET` | `/alertas/roas` | Campanhas com ROAS problemático (`?threshold=`) |
| `GET` | `/evolucao` | Evolução diária (`?campanha=&data_inicio=&data_fim=`) |
| `GET` | `/campanha/{nome}` | Resumo de uma campanha |
| `GET` | `/campanha/{nome}/funil` | Funil de conversão de uma campanha |

### Consulta inteligente

| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/consulta` | Consulta em linguagem natural `{"pergunta": "...", "thread_id": "..."}` |
| `POST` | `/sql` | SQL direto (apenas SELECT) `{"sql": "SELECT ..."}` |

### Manutenção

| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/db/reload` | Recarrega o CSV no banco (idempotente) |

### Notion

| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/notion/status` | Status da conexão e lista de databases |
| `GET` | `/notion/databases` | Lista databases configurados |
| `GET` | `/notion/relatorios/{cliente}` | Relatórios de um cliente (`?limit=&data_inicio=`) |
| `POST` | `/notion/publicar` | Publica novo relatório no Notion |
| `POST` | `/notion/buscar` | Busca livre no workspace |

---

## Integração Notion

### Workspace
- **Conta:** Guará Media
- **Bot:** API_AGENTE_DE_PERFORMANCE
- **Versão da API:** 2025-09-03

### Databases configurados

| Chave | Database | Uso |
|-------|---------|-----|
| `source_of` | Relatórios — Source OF | **Padrão** — todos os dados são Source OF |
| `consorcio_sorteado` | Relatórios — Consórcio Sorteado | Outros clientes Guará |
| `maximo_servico` | Relatórios — Máximo Serviço | |
| `be_mushin` | Relatórios — BE MUSHIN | |
| `bazar_precatorio` | Relatórios — Bazar do Precatório | |
| `torre_controle` | Torre de Controle — Regras | |
| `clientes` | Clientes | |

### Estrutura da página gerada
Cada relatório publicado no Notion contém:
- **Callout** com cliente, plataforma, pergunta original e data de geração
- **Métricas** em bullets (investimento, receita, ROAS, compras, CTR, CPC)
- **Tabela** dos dados em bloco de código ASCII
- **Toggle** com série diária (para relatórios de campanha)
- **Taxas de conversão** (para relatórios de funil)
- **Rodapé** com fonte e data

---

## Variáveis de Ambiente

Arquivo: `agente-performance/.env`

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `ANTHROPIC_API_KEY` | Chave API Claude (Anthropic) | obrigatório |
| `SLACK_BOT_TOKEN` | Token do bot Slack (`xoxb-...`) | obrigatório |
| `SLACK_SIGNING_SECRET` | Segredo de verificação Slack | obrigatório |
| `NOTION_API_KEY` | Token de integração Notion (`ntn_...`) | obrigatório |
| `PORT` | Porta do servidor | `8001` |
| `CLAUDE_MODEL` | Modelo Claude a usar | `claude-haiku-4-5-20251001` |
| `CACHE_TTL_SECONDS` | TTL do cache em memória | `3600` (1h) |
| `ROAS_ALERT_THRESHOLD` | ROAS mínimo para alertas | `1.5` |
| `NO_CONVERSION_DAYS` | Dias sem conversão para alerta | `3` |
| `LOG_LEVEL` | Nível de log | `INFO` |
| `NOTION_VERSION` | Versão da API Notion | `2025-09-03` |

---

## Serviço systemd

```bash
# Status
systemctl status agente-guara.service

# Reiniciar (após mudanças no código)
sudo systemctl restart agente-guara.service

# Logs em tempo real
journalctl -u agente-guara.service -f

# Recarregar banco de dados (novo CSV)
curl -X POST http://localhost:8001/db/reload
```

Arquivo: `/etc/systemd/system/agente-guara.service`
```ini
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/.openclaw/workspace/agente-performance
ExecStart=/home/ubuntu/.openclaw/workspace/agente-performance/venv/bin/python main.py
Restart=always
```

---

## Apache (Proxy Reverso)

Configuração em `/etc/apache2/sites-enabled/flora-webhook.conf`:

```apache
# Agente de Performance Guará
ProxyPass        /guara/slack/events  http://127.0.0.1:8001/slack/events
ProxyPassReverse /guara/slack/events  http://127.0.0.1:8001/slack/events
ProxyPass        /guara/             http://127.0.0.1:8001/
ProxyPassReverse /guara/             http://127.0.0.1:8001/
```

URL pública da API: `https://44.204.251.251/guara/`
URL do Slack Events: `https://44.204.251.251/guara/slack/events`

---

## Configuração do Slack App

Acesse [api.slack.com/apps](https://api.slack.com/apps) e configure:

### Event Subscriptions
- **Request URL:** `https://44.204.251.251/guara/slack/events`
- **Subscribe to bot events:** `app_mention`, `message.im`

### OAuth Scopes (Bot Token)
`chat:write` · `reactions:write` · `reactions:read` · `app_mentions:read` · `im:history` · `files:write`

### Slash Commands
Cada comando abaixo deve ser cadastrado com **Request URL** `https://44.204.251.251/guara/slack/events`:

| Comando | Descrição curta |
|---------|----------------|
| `/ajuda` | Menu de ajuda |
| `/hoje` | Resumo do dia |
| `/semana` | Resumo da semana |
| `/mes` | Resumo do mês |
| `/top` | Top campanhas por ROAS |
| `/campanha` | Performance de campanha |
| `/campanhas` | Lista de campanhas |
| `/alertas` | Alertas de performance |
| `/funil` | Funil de conversão |
| `/compare` | Compara campanhas |
| `/relatorio` | Exporta relatório |

---

## Fluxo de Processamento

```
Mensagem Slack
      │
      ▼
[handlers.py] ACK imediato + reação ⏳
      │
      ├── Pedido de ajuda? → retorna AJUDA_TEXTO
      ├── Formato detectado? → (excel / pdf / notion / excel+notion)
      │
      ▼
[engine.consultar()]
      │
      ├── 1. Verifica cache (md5 da pergunta)
      │       └── HIT → retorna imediatamente
      │
      ├── 2. NLP com Claude (agent/nlp.py)
      │       └── retorna: intents[], campanha, data_inicio, data_fim, limit
      │
      ├── 3. Valida campanha
      │       ├── fuzzy_match_campaign() (rapidfuzz, threshold 60)
      │       └── não encontrada → erro com lista de sugestões
      │
      ├── 4. Executa query SQL (agent/queries.py)
      │       └── retorna dados como list[dict]
      │
      ├── 5. Dados vazios?
      │       └── sugere período com dados disponíveis
      │
      └── 6. Salva no cache (se não vazio)
      │
      ▼
[claude_client.gerar_resposta()]
      │
      └── Claude formata resposta em mrkdwn com insights
      │
      ▼
[formatter.markdown_to_blocks()]
      │
      └── Converte para Block Kit nativo do Slack
      │
      ▼
[client.chat_postMessage(blocks=...)]
      │
      ▼
[finally: conversation_log.registrar()]
      └── Registra sempre: pergunta, resposta, intent, latência, cache_hit, erro
```

---

## Modelo de Dados (SQLite)

Tabela: `campanhas`

### Colunas principais

| Coluna SQL | Descrição |
|-----------|-----------|
| `data_inicio` | Data de início do período |
| `data_fim` | Data de fim do período |
| `campanha` | Nome da campanha |
| `investimento` | Valor investido (R$) |
| `valor_compras` | Receita gerada (R$) |
| `compras` | Número de compras |
| `roas_compras` | ROAS calculado (receita / investimento) |
| `impressoes` | Impressões totais |
| `alcance` | Pessoas alcançadas únicas |
| `cliques_link` | Cliques no link |
| `ctr_link` | CTR do link |
| `cpc_link` | CPC do link |
| `cpm` | Custo por mil impressões |
| `frequencia` | Frequência média por pessoa |
| `custo_por_compra` | CPO (investimento / compras) |
| `adicoes_carrinho` | Adds ao carrinho |
| `checkout_iniciado` | Checkouts iniciados |

---

## Dependências

```
fastapi>=0.110          # Framework REST
uvicorn>=0.29           # Servidor ASGI
slack-bolt>=1.18        # SDK Slack Bolt async
anthropic>=0.25         # Cliente Anthropic Claude
rapidfuzz>=3.6          # Fuzzy matching para nomes de campanha
python-dotenv>=1.0      # Variáveis de ambiente
aiohttp>=3.9            # HTTP assíncrono
openpyxl>=3.1           # Geração de Excel
reportlab>=4.1          # Geração de PDF
requests>=2.31          # HTTP síncrono (Notion API)
pydantic>=2.6           # Validação de dados
```

---

## Desenvolvimento

### Instalar dependências
```bash
cd agente-performance
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Carregar banco de dados
```bash
# Coloca o CSV em data/campanhas.csv e executa:
python -c "from db.loader import load_csv; print(load_csv(), 'registros')"

# Ou via API (com serviço rodando):
curl -X POST http://localhost:8001/db/reload
```

### Rodar localmente
```bash
python main.py
# Servidor disponível em http://localhost:8001
# Documentação Swagger: http://localhost:8001/docs
```

### Testar consultas
```bash
# Consulta via API
curl -X POST http://localhost:8001/consulta \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "top 5 campanhas por ROAS em junho 2026", "thread_id": "test"}'

# Status Notion
curl http://localhost:8001/notion/status

# Relatórios Source OF do Notion
curl "http://localhost:8001/notion/relatorios/source_of?limit=5"
```

---

## Regras e Restrições

- **Flora é intocável** — nunca modificar `Flora-Agente*/`, `flora-*.service`, porta 5000 ou `flora-webhook.conf`
- **Apenas SELECT** — `consultar_sql()` rejeita qualquer SQL que não seja SELECT
- **Nunca inventar dados** — instrução explícita no system prompt do Claude
- **Banco Source OF** — todos os dados são exclusivamente Source OF; relatórios sempre identificados com o cliente

---

## Histórico de Versões

| Data | Versão | Mudanças |
|------|--------|---------|
| 2026-06-23 | 2.3.0 | Identificação Source OF em todos os relatórios |
| 2026-06-23 | 2.2.0 | Exportação para Notion com link retornado no Slack |
| 2026-06-23 | 2.1.0 | Integração Notion — 7 databases + 5 endpoints REST |
| 2026-06-23 | 2.0.2 | 10 slash commands + `/ajuda` atualizado |
| 2026-06-23 | 2.0.1 | `conversation_log` integrado no handler Slack |
| 2026-06-23 | 2.0.0 | Block Kit nativo; fuzzy em `performance_campanha`; alertas ROAS com NULL |
| 2026-06-22 | 1.3.0 | Comparativos: WoW, MoM, antes/depois, ranking |
| 2026-06-22 | 1.2.0 | Cache, logging, validação de campanha, export Excel/PDF |
| 2026-06-22 | 1.1.0 | NLP com Claude + fuzzy matching + memória de contexto |
| 2026-06-22 | 1.0.0 | Lançamento inicial — FastAPI + Slack Bolt + SQLite |
