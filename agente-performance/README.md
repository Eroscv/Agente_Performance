# ⚙️ Agente de Performance Guará

Agente de análise de performance de campanhas Meta Ads para os clientes da **Guará Marketing**.  
Processa dados de CSV → SQLite → gera relatórios automáticos no **Notion**.

---

## 📐 Regras de Negócio (definidas em reunião com Bianca/Eros)

| Métrica | Regra |
|---|---|
| **Fator de Imposto** | `1,1215` — aplicado sobre todo investimento Meta Ads |
| **Investimento ajustado** | `spend × 1,1215` |
| **ROAS** | `Receita Shopify ÷ Investimento ajustado` — **nunca usar o ROAS do Meta** |
| **CPA** | `Investimento ajustado ÷ Número de compras` — **nunca usar o do Meta** |
| **CAC** | `Investimento ajustado ÷ Novos clientes (Shopify via UTM)` |
| **CPC** | Direto da plataforma Meta — **não recalcular** |
| **Ticket Médio** | `Receita ÷ Transações (Shopify)` |

> ⚠️ O valor `1,1225` foi citado em outro momento — **ignorar. O correto é 1,1215.**

---

## 🗃️ Fonte de Dados

- **CSV Meta Ads** → carregado no SQLite local (`data/campanhas.db`)
- Colunas normalizadas pelo `db/schema.py` (mapeamento PT-BR → SQL)
- Carga idempotente via `db/loader.py` (truncate + insert)
- **MVP usa exclusivamente dados disponíveis no CSV** — sem Shopify externo nesta fase

---

## 🏗️ Arquitetura

```
agente-performance/
├── agent/          # Engine de consulta NLP + queries SQLite
├── api/            # Endpoint FastAPI (recebe perguntas via Slack)
├── data/           # CSV de campanhas + banco SQLite gerado
├── db/             # Schema, loader e conexão SQLite
├── notion/         # Cliente Notion + gerador de relatórios
├── relatorio/      # Regras de negócio (regras.py) + builder Meta (meta.py)
├── reports/        # Exportador auxiliar
├── slack/          # Formatadores e handlers Slack
└── main.py         # Entry point
```

---

## 🗺️ Plano de MVP — Entregas por Etapa

### ✅ Etapa 0 — Fundação (concluída)
> Base de dados e regras prontas para construir.

- [x] Normalização do CSV Meta Ads no SQLite (`db/schema.py`, `db/loader.py`)
- [x] Regras de negócio definidas e implementadas (`relatorio/regras.py`)
  - Fator de imposto `1,1215`
  - Cálculo de ROAS, CAC, CPA com spend ajustado
  - CPC direto da plataforma
- [x] Builder de dados Meta Ads (`relatorio/meta.py`)
  - `tabela_principal()` — métricas por campanha
  - `alocacao_budget()` — distribuição percentual do orçamento
  - `top_ads()` — anúncios mais investidos com cruzamento Shopify
- [x] Cliente Notion configurado (`notion/client.py`)
  - Databases mapeados: Source OF, Consórcio Sorteado, Máximo Serviço, BE MUSHIN, Bazar do Precatório
- [x] Gerador de páginas Notion (`notion/relatorio.py`)
  - Blocos: heading, bullet, callout, toggle, tabela ASCII, divisor

---

### 🚧 Etapa 1 — Relatório Semanal Automático (em construção)
> Objetivo: publicar um relatório completo no Notion com 1 comando.

**Entregas:**

- [ ] **`relatorio/builder.py`** — orquestra a geração do relatório semanal
  - Lê dados do SQLite (período configurável: última semana)
  - Aplica regras de negócio (regras.py + meta.py)
  - Monta estrutura de blocos para o Notion
- [ ] **Página Notion estruturada** com seções:
  - 📊 Resumo Geral da Conta (totais de investimento, impressões, cliques, CPC, CPM)
  - 📈 Tabela de Campanhas com métricas recalculadas (ROAS, CPA, CPC plataforma)
  - 💰 Alocação de Budget (% por campanha)
  - 🏆 Top Campanhas por ROAS
  - 🔴 Alertas de ROAS baixo (< threshold configurável)
- [ ] **`scripts/gerar_relatorio.py`** — CLI para disparo manual
  ```bash
  python scripts/gerar_relatorio.py --cliente source_of --periodo semana
  ```
- [ ] Publicação no database correto por cliente (`DATABASES` map)

**Critério de aceite:** rodar o script → página aparecer no Notion com dados reais do CSV.

---

### 📬 Etapa 2 — Disparo via Slack (MVP interativo)
> Objetivo: responder perguntas sobre campanhas no Slack e publicar o resultado no Notion.

**Entregas:**

- [ ] Integração Slack Bolt funcional (`slack/handlers.py`)
- [ ] NLP básico para interpretar perguntas (`agent/nlp.py`, `agent/synonyms.py`)
  - Exemplos: *"como foi a semana?", "top campanhas por ROAS", "funil da campanha X"*
- [ ] Engine de consulta SQL dinâmica (`agent/engine.py`, `agent/queries.py`)
- [ ] Resposta no Slack: resumo em texto + link para página no Notion
- [ ] Suporte a períodos: hoje, ontem, semana, mês, intervalo personalizado

**Critério de aceite:** perguntar no Slack → receber resposta com link Notion em < 10s.

---

### 🔄 Etapa 3 — Relatório Agendado (produção)
> Objetivo: relatório publicado automaticamente toda segunda-feira.

**Entregas:**

- [ ] Cron job (via APScheduler ou cron Linux) para disparo semanal
- [ ] Upload automático do CSV via API Meta Ads (substituindo upload manual)
- [ ] Atualização incremental do SQLite (sem recriar do zero)
- [ ] Notificação no Slack quando o relatório for publicado
- [ ] Suporte a múltiplos clientes (Consórcio Sorteado, Máximo Serviço, etc.)

---

### 🧠 Etapa 4 — Memória e Insights (pós-MVP)
> Objetivo: o agente aprende com o histórico e gera insights proativos.

**Entregas:**

- [ ] Base de Conhecimento Notion (`notion/knowledge_base.py`) — aprovação via 👍 no Slack
- [ ] Comparativos automáticos: semana atual vs anterior, mês atual vs anterior
- [ ] Detecção de anomalias (queda brusca de ROAS, CPM subindo, etc.)
- [ ] Sugestões de ação baseadas no histórico aprovado

---

## 🔧 Setup

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Configurar variáveis de ambiente
cp .env.example .env
# Preencher: NOTION_API_KEY, ANTHROPIC_API_KEY, SLACK_BOT_TOKEN, etc.

# 3. Carregar CSV no SQLite
python -m db.loader

# 4. Testar conexão Notion
python -m notion.client
```

---

## 📊 Databases Notion Configurados

| Cliente | Database ID |
|---|---|
| Source OF | `37a8a727-a8ff-81ba-aecd-e3ffff1f7f7e` |
| Consórcio Sorteado | `37a8a727-a8ff-814c-a1dc-e5e0b9f5993a` |
| Máximo Serviço | *(obter na primeira chamada)* |
| BE MUSHIN | *(obter na primeira chamada)* |
| Bazar do Precatório | *(obter na primeira chamada)* |

---

## 📁 Arquivos Chave

| Arquivo | Função |
|---|---|
| `relatorio/regras.py` | Constantes e cálculos de negócio (imposto, ROAS, CAC, CPA) |
| `relatorio/meta.py` | Builder de tabelas Meta Ads com regras aplicadas |
| `notion/client.py` | Cliente HTTP Notion + helpers de propriedades |
| `notion/relatorio.py` | Gerador de páginas Notion (blocos, formatação) |
| `db/schema.py` | Mapeamento CSV PT-BR → colunas SQL |
| `db/loader.py` | Carga/atualização do CSV no SQLite |
| `agent/engine.py` | Engine principal de consulta NLP → SQL → resultado |

---

*Desenvolvido para Guará Marketing — uso interno.*
