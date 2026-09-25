# 📋 Plano de Projeto — Agente de Performance Guará

> Documento vivo. Atualizado em: 25/09/2026 · Responsável: Eros (Guará Marketing)

---

## Definição

**Objetivo e entregável final:**
Sistema de análise de mídia paga com IA que responde perguntas em linguagem natural via Slack, sincroniza dados de Meta Ads, Google Ads e Shopify, e publica relatórios automáticos diários, semanais e mensais no Notion e Slack — para os clientes da Guará Marketing.

**Dentro do escopo:**
- Integração Meta Ads API (Source OF + POD Kombucha)
- Integração Google Ads API (Source OF)
- Integração Shopify API (Source OF + POD Kombucha)
- Bot Slack conversacional (perguntas em português, NLP + Claude Haiku)
- Relatórios automáticos: diário, semanal, mensal (Slack + Notion)
- Alertas proativos de ROAS
- ROAS real calculado com fator de imposto Meta (×1,1215)
- Data Warehouse local (SQLite + DuckDB + Parquet, star schema)
- Sincronização horária de dados via crons OpenClaw
- Integração Notion (publicação de relatórios por cliente/data)
- Dashboard via Obsidian (métricas locais)

**Fora do escopo:**
- Interface web própria (usa Slack como front-end)
- Multi-agência (focado exclusivamente na Guará Marketing)
- Dados de TikTok Ads, Pinterest Ads ou outras plataformas não listadas
- Atendimento ao cliente final (não é um chatbot B2C)
- Previsões/modelos preditivos de ML (análise descritiva e comparativa apenas)

**Data de início:** 23/06/2026
**Data alvo de conclusão:** Em produção contínua — próximo marco: retomada dos syncs após renovação de tokens (estimativa: 28/09/2026)

---

## Fases

### Fase 1 — Fundação e MVP Conversacional
- **Entregável:** Bot Slack funcional respondendo perguntas sobre Meta Ads e Google Ads com dados reais, armazenados em SQLite local
- **Critério de pronto:** Agente responde corretamente perguntas de ROAS, investimento, top campanhas e comparativos para pelo menos 1 cliente ativo
- **Responsável:** Eros / Cesar (agente)
- **Duração estimada:** 3 semanas
- **Gate que fecha a fase:** Primeiro relatório diário postado no Slack com dados reais · **Data:** 23/06/2026 → 14/07/2026 ✅

---

### Fase 2 — Multi-cliente e Shopify
- **Entregável:** Suporte a Source OF e POD Kombucha simultaneamente; ROAS real calculado com receita Shopify; relatório diário multi-plataforma no Slack e Notion
- **Critério de pronto:** Relatório diário entregue para ambos os clientes com dados de Meta + Google + Shopify, sem intervenção manual
- **Responsável:** Eros / Cesar (agente)
- **Duração estimada:** 4 semanas
- **Gate que fecha a fase:** Primeiro ciclo completo automático (sync → builder → Slack → Notion) para os 2 clientes · **Data:** 14/07/2026 → 19/08/2026 ✅

---

### Fase 3 — Qualidade de Dados e Data Warehouse
- **Entregável:** Guardrails de qualidade (volume mínimo, queda brusca, janela de atribuição), ETL para DuckDB + Parquet, star schema local, dual-write BigQuery
- **Critério de pronto:** Zero relatórios enviados com dados parciais ou incompletos; banco histórico completo no DuckDB; BigQuery espelhando SQLite
- **Responsável:** Eros / Cesar (agente)
- **Duração estimada:** 3 semanas
- **Gate que fecha a fase:** 201 testes automatizados passando + QA_REPORT.md validado · **Data:** 19/08/2026 → 25/09/2026 ✅ _(concluído, tokens expirados abriram novo bloqueio)_

---

### Fase 4 — Estabilização e Retomada _(atual)_
- **Entregável:** Tokens renovados, syncs validados, crons reativados, alertas de ROAS com lógica aprimorada (2 dias consecutivos antes de disparar)
- **Critério de pronto:** 3 dias consecutivos de relatórios automáticos entregues sem erro; nenhum alerta falso positivo no Slack
- **Responsável:** Eros (renovação de tokens) / Cesar (reativação e validação)
- **Duração estimada:** 3-5 dias úteis
- **Gate que fecha a fase:** Primeiro ciclo limpo pós-renovação · **Data alvo:** 28/09/2026 · **Status:** 🔴 Bloqueado (tokens expirados)

---

### Fase 5 — Multi-Agentes _(planejado)_
- **Entregável:** Arquitetura OrchestratorAgent + MetaAdsAgent + GoogleAdsAgent + ReportAgent; suporte a 3+ clientes e 2+ plataformas novas sem regressão
- **Critério de pronto:** Todos os clientes atuais funcionando na nova arquitetura; queries paralelas via `asyncio.gather()`
- **Responsável:** Cesar (agente)
- **Duração estimada:** 4-6 semanas
- **Gate que fecha a fase:** 3+ clientes ativos em produção simultânea · **Data alvo:** A definir · **Status:** ⏳ Aguarda gate de entrada (3+ clientes ativos)

---

## Linha de Gates

| Gate | Escopo | Data alvo | Status |
|---|---|---|---|
| **Gate 1 — Fundação** | MVP conversacional + 1 cliente + relatório no Slack | 14/07/2026 | ✅ Aprovado |
| **Gate 2 — Multi-cliente** | Source OF + POD Kombucha + Shopify + Notion | 19/08/2026 | ✅ Aprovado |
| **Gate 3 — Qualidade** | Guardrails + DuckDB + BigQuery + 201 testes | 25/09/2026 | ✅ Aprovado |
| **Gate 4 — Retomada** | Tokens renovados + 3 dias limpos + alertas calibrados | 28/09/2026 | 🔴 Bloqueado |
| **Gate 5 — Multi-agentes** | 3+ clientes em produção simultânea | A definir | ⏳ Aguarda |

---

## Documentação

**Onde fica o código:**
`/home/ubuntu/.openclaw/workspace/agente-performance/`
GitHub: https://github.com/Eroscv/Agente_Performance _(privado)_

**Onde fica a doc técnica:**
- `README.md` — visão geral e estrutura
- `STATUS.md` — estado atual, bloqueantes e próximos passos
- `DAILY_REPORT.md` — log de sprints e decisões técnicas (gerado em 19/08/2026)
- `FASE2.md` — arquitetura planejada para multi-agentes
- `docs/BIGQUERY.md` — arquitetura dual-write, tabelas, queries
- `alteracoes/` — log detalhado de cada commit relevante
- `QA_REPORT.md` — 39 casos manuais + 201 testes automatizados

**Acessos e ambientes:**

| Recurso | Detalhe |
|---|---|
| Servidor | `44.204.251.251` (AWS EC2, Ubuntu) |
| Serviço | `agente-guara.service` (porta 8001) — _não existe como service systemd; roda via crons OpenClaw_ |
| Slack | Canal `#performance` da Guará Marketing |
| Notion | Workspace Guará — páginas por cliente/data |
| Meta Ads | Conta `act_324906945710366` — token em `.env` (`META_ACCESS_TOKEN`) |
| Google Ads | Customer ID Source OF — token em `.env` (`GOOGLE_ADS_DEVELOPER_TOKEN`) |
| Shopify Source OF | `sourceof.myshopify.com` — token em `.env` (`SHOPIFY_ACCESS_TOKEN`) |
| Shopify POD | `d5bead-1b.myshopify.com` — token em `.env` (`SHOPIFY_POD_ACCESS_TOKEN`) ⚠️ expirado |
| BigQuery | Projeto Guará — credenciais em `.env` (`GOOGLE_APPLICATION_CREDENTIALS`) |
| OpenClaw crons | 15 jobs gerenciados via OpenClaw scheduler |

---

## Riscos conhecidos

- **Token Meta expirado** (ativo agora) — tokens de System User Meta às vezes são revogados por inatividade ou mudança de política. Mitigação: documentar renovação e criar alerta de monitoramento de validade do token.
- **Token Shopify POD expirado** (ativo agora) — API keys Shopify privadas não expiram por padrão, mas podem ser revogadas. Investigar causa antes de renovar.
- **Alertas ROAS com dados velhos** — quando o sync falha, o banco retém dados anteriores e os alertas continuam disparando como se fossem novos. Mitigação: adicionar verificação de freshness dos dados antes de disparar alertas.
- **Dependência de crons OpenClaw** — todos os 15 jobs dependem do OpenClaw estar rodando. Se o processo cair, nenhum sync ocorre silenciosamente. Mitigação: adicionar healthcheck externo.
- **Crescimento de clientes trava a arquitetura atual** — com 3+ clientes, o modelo monolítico atual começa a apresentar contenção. Gate 5 resolve isso com a arquitetura multi-agentes.
- **BigQuery dual-write sem retry robusto** — falhas no BigQuery não bloqueiam o fluxo principal (silenciosas). Pode gerar divergência entre SQLite e BQ. Monitorar.

---

## Log de mudanças no plano

| Data | O que mudou | Motivo | Quem aprovou |
|---|---|---|---|
| 23/06/2026 | Início do projeto — MVP Meta Ads + Slack | Necessidade Guará Marketing | Eros |
| 14/07/2026 | Adição Google Ads ao escopo | Source OF usa ambas as plataformas | Eros |
| 19/07/2026 | Adição Shopify ao escopo | ROAS real exige receita Shopify, não apenas conversão Meta | Eros |
| 19/08/2026 | DuckDB + star schema adicionados ao escopo | Queries analíticas complexas lentas no SQLite | Eros |
| 19/08/2026 | Guardrails de qualidade adicionados ao escopo | Relatórios com dados parciais chegando ao Slack | Eros |
| 25/09/2026 | Todos os 15 crons desativados | Token Meta + Token Shopify POD expiraram; alertas de ROAS gerando ruído com dados velhos | Eros |
| 25/09/2026 | Alerta ROAS desativado permanentemente até revisão da lógica | Disparando com dados velhos sem freshness check | Eros |

---

*Mantido por Cesar ⚙️ — Guará Marketing*
