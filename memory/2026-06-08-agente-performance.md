# Session: 2026-06-08 17:54:13 UTC

- **Session Key**: agent:main:subagent:7df30e3b-799f-4598-a3d2-18fcd64f69da
- **Session ID**: 83f3e741-ca7f-41da-9679-bcd450a8bcf0
- **Source**: webchat

## Conversation Summary

user: [Mon 2026-06-08 16:52 UTC] [Subagent Context] You are running as a subagent (depth 1/1). Results auto-announce to your requester; do not busy-poll for status.

[Subagent Task]: Você é Cesar, Engenheiro Sênior. Construa o Agente de Performance Guaraná completo conforme especificação abaixo.

## IMPORTANTE — Credenciais (use APENAS no .env, nunca logue ou exponha)
- ANTHROPIC_API_KEY: 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
- SLACK_SIGNING_SECRET: f1b5b44fe15756e3cf6294e1b0f746fc
- SLACK_BOT_TOKEN: xoxb-5651931611700-11055526950803-AEjX5zA31OPP2MlCFGuCgH8u
- WINDSOR_API_KEY: 3c1bde18818cd2bf5cf52a91fb5bc4634c1a

## Diretório de trabalho
/workspace/agente-performance/

## Stack
- Python 3.11+, FastAPI, Slack Bolt (AsyncApp), Anthropic SDK, httpx, APScheduler, aiosqlite, python-dotenv, uvicorn

## Estrutura modular COMPLETA a criar:

```
agente-performance/
├── agente_guara.py
├── .env
├── .env.example
├── requirements.txt
├── db.py                        # shim: from storage.db import *
├── slack/
│   ├── __init__.py
│   ├── handlers.py
│   └── formatter.py
├── agent/
│   ├── __init__.py
│   ├── processor.py
│   ├── intent_detection.py
│   ├── query_routing.py
│   ├── data_processing.py
│   ├── claude_client.py
│   ├── change_detector.py
│   └── anomaly_detector.py
├── clients/
│   ├── __init__.py
│   └── windsor_client.py
├── scheduling/
│   ├── __init__.py
│   ├── core.py
│   ├── runner.py
│   ├── preset_jobs.py
│   ├── flora_jobs.py
│   └── performance_jobs.py
├── reports/
│   ├── __init__.py
│   ├── aggregation.py
│   ├── helpers.py
│   ├── flora_report.py
│   └── performance_report.py
├── storage/
│   ├── __init__.py
│   ├── db.py
│   └── database.py
├── parsing/
│   ├── __init__.py
│   └── period_parser.py
└── prompts/
    ├── __init__.py
    ├── system_prompt.py
    └── insight_prompts.py
```

## Requisitos funcionais detalhados

### agente_guara.py
- FastAPI app com lifespan (startup/shutdown)
- Integra Slack Bolt via SlackRequestHandler
- Rotas: POST /slack/events, GET /health, POST /reports/send/{cliente}, GET /cache/{cliente}/status, POST /cache/{cliente}/warm, POST /webhook/windsor, GET+PUT /clients/{id}, POST /leads/qualify, POST /backfill/{cliente}
- No startup: inicia scheduler, aquece cache (fire-and-forget), inicializa DB
- No shutdown: para scheduler gracefully
- Roda com uvicorn na porta definida em PORT (default 8000)

### slack/handlers.py
- Bolt AsyncApp com signing_secret e bot_token do env
- Handler para @mentions (app_mention) e mensagens diretas (message)
- Dedupe por event_id com TTL 60s usando tabela SQLite event_dedupe
- ACK imediato (< 3s) — processa em asyncio background task
- Chama processor.py para gerar resposta
- Responde na thread original

### slack/formatter.py
- Formata respostas em Block Kit (section, divider, context)
- Converte tabelas markdown em blocos de código formatados
- Trunca mensagens muito longas com link "ver mais"
- Adiciona emojis contextuais (📊 métricas, ⚠️ alerta, ✅ ok, 📈 crescimento, 📉 queda)

### agent/intent_detection.py
- Detecta: cliente (por nome/alias), período (delega a period_parser), métrica solicitada (ROAS, CPL, CPC, CTR, impressões, cliques, conversões, gasto), tipo de análise (comparação, tendência, alerta, resumo)
- Retorna dict estruturado: {cliente, periodo, metricas, tipo_analise, comparar_com}
- Usa regex + heurísticas simples (sem LLM para não adicionar latência)

### agent/query_routing.py
- Decide fonte de dados baseado na intenção detectada
- Rotas: windsor_only, db_only, windsor+db, knowledge_base
- Considera se dados estão em cache antes de rotear

### agent/data_processing.py
- Normaliza resposta da Windsor.AI em estrutura padronizada
- Agrega por plataforma, campanha, período
- Calcula métricas derivadas: ROAS = receita/gasto, CPL = gasto/leads, CPC = gasto/cliques
- Compara períodos quando solicitado (variação % com sinal)
- Retorna dict com métricas_atual, métricas_anterior (se comparação), variações

### agent/claude_client.py
- Wrapper async do Anthropic SDK
- Semáforo asyncio para limitar concorrência (CLAUDE_MAX_CONCURRENCY, default 3)
- Monta mensagens com system prompt + dados contextualizados
- Retry com backoff em caso de rate limit (429)
- Extrai e salva insights em fire-and-forget

### agent/change_detector.py
- Compara snapshot atual com snapshot anterior (tabela campaign_snapshots)
- Detecta: campanhas pausadas/ativadas, budget alterado >20%, bid alterado, nova campanha, campanha removida
- Salva em campaign_changes com severidade (info/warning/critical)

### agent/anomaly_detector.py
- Detecta anomalias estatísticas comparando com média dos últimos 7 dias
- Threshold configurável: desvio >2σ = anomalia
- Métricas monitoradas: ROAS, CPL, CTR, gasto diário
- Salva em anomaly_log

### agent/processor.py
- Pipeline completo: recebe texto da pergunta + contexto Slack
- Chama intent_detection → query_routing → windsor_client/db → data_processing → claude_client → formatter
- Em caso de erro em qualquer etapa: resposta degradada amigável
- Fire-and-forget: change_detector + anomaly_detector após cada busca bem-sucedida

### clients/windsor_client.py
- Cliente httpx async para https://connectors.windsor.ai/all e /refresh
- Parâmetros: api_key, fields (lista de métricas), date_from, date_to, filters
- Cache 2 camadas:
  - L1: dict em memória com TTL absoluto (por preset)
  - L2: tabela SQLite api_cache com expires_at
- Circuit breaker:
  - Estado em memória: CLOSED/OPEN/HALF_OPEN
  - 5 falhas consecutivas → OPEN por 5 min (CIRCUIT_BREAKER_COOLDOWN)
  - HALF_OPEN: testa 1 req; sucesso → CLOSED, falha → OPEN
  - Fallback: retorna cache stale do SQLite com flag is_stale=True
- Método fetch_with_cache(preset, bypass_cache, force_refresh, prewarmed)
- Método refresh() para forçar re-sync com plataformas
- Método get_circuit_status() para health check

### storage/db.py
- aiosqlite para todas as operações
- CREATE TABLE IF NOT EXISTS para todas as tabelas (schema versionado)
- Tabelas: api_cache, knowledge_base, client_profiles, lead_qualifications, campaign_changes, cache_warm_log, message_outbox, event_dedupe, anomaly_log, alert_history, campaign_snapshots, report_history, windsor_refresh_log, client_metrics_cache, conversation_context, scheduled_job_log, budget_alerts, platform_health, data_backfill_log, user_preferences
- Funções CRUD async para cada tabela
- Função init_db() que cria todas as tabelas

### storage/database.py
- Funções complementares: search_knowledge_base(query), get_recent_insights(cliente, limit), get_client_stats(cliente), update_platform_health(plataforma, status), get_pending_outbox(limit)

### parsing/period_parser.py
- parse_period(texto) → {date_from: str, date_to: str, preset: str, label: str}
- Suporta: last_Xd, WTD, MTD, YTD, "hoje", "ontem", "esta semana", "semana passada", "este mês", "mês passado"
- Detecta comparações: "compare X com Y" → retorna periodos=[periodo1, periodo2]
- Todas as datas no formato YYYY-MM-DD, timezone America/Sao_Paulo

### prompts/system_prompt.py
- SYSTEM_PROMPT_ANALYST: analista de mídia paga, responde em pt-BR, foco em insights acionáveis
- SYSTEM_PROMPT_REPORT: gerador de relatórios estruturados
- build_context_prompt(dados, cliente, periodo) → monta prompt com os dados fornecidos

### prompts/insight_prompts.py
- INSIGHT_EXTRACTION_PROMPT: extrai insights principais de um conjunto de dados
- ANOMALY_ANALYSIS_PROMPT: analisa anomalias detectadas
- CHANGE_IMPACT_PROMPT: avalia impacto de alterações em campanhas

### scheduling/core.py
- BRT = pytz.timezone("America/Sao_Paulo")
- get_next_run_times(scheduler) → lista de próximos jobs
- format_job_status(job) → dict serializable para health check

### scheduling/runner.py
- setup_scheduler() → AsyncIOScheduler com todos os jobs registrados
- Importa e registra jobs de flora_jobs, performance_jobs, preset_jobs
- Cada grupo com offset de +1,+2,+3 min para evitar rajadas

### scheduling/preset_jobs.py
- warm_all_presets(): dispara 1 refresh compartilhado + busca paralela para todos presets × plataformas
- Registra resultado em cache_warm_log (ok/vazio/erro + nº registros)
- Agendado a cada hora

### scheduling/flora_jobs.py
- send_flora_weekly_report(): seg 08:01 BRT
- send_flora_wtd_report(): qua e sex 08:02 BRT
- Chama flora_report.py para montar conteúdo e envia via Slack

### scheduling/performance_jobs.py
- check_roas_alerts(): diário 09:00 e 15:00 BRT
- check_zero_spend(): diário 10:00 BRT
- check_no_conversions(): diário 10:01 BRT (X dias = 3, configurável)
- run_anomaly_check(): a cada 4h
- retry_outbox(): a cada 5min
- cleanup_old_data(): diário 03:00 BRT (remove registros > 90 dias de logs)
- validate_campaign_changes(): a cada 30min

### reports/aggregation.py
- aggregate_by_platform(dados) → métricas por plataforma
- aggregate_by_campaign(dados) → top N campanhas por gasto
- calculate_period_summary(dados) → totais + médias do período
- compare_periods(period1, period2) → variações com %

### reports/helpers.py
- format_currency(valor) → "R$ 1.234,56"
- format_percentage(valor) → "+12,3%" ou "-5,1%"
- format_roas(valor) → "3.45x"
- build_report_header(cliente, periodo) → texto de cabeçalho
- build_metrics_table(dados) → tabela markdown

### reports/flora_report.py
- generate_flora_report(tipo="weekly"|"wtd") → str com relatório formatado
- Busca dados Windsor para cliente Flora
- Usa aggregation.py + helpers.py + claude_client para insights finais

### reports/performance_report.py
- generate_performance_report(cliente, tipo) → str com relatório formatado
- Estrutura genérica reutilizável para múltiplos clientes

### .env
Preencha com as credenciais fornecidas:
- ANTHROPIC_API_KEY=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
- SLACK_SIGNING_SECRET=f1b5b44fe15756e3cf6294e1b0f746fc
- SLACK_BOT_TOKEN=xoxb-5651931611700-11055526950803-AEjX5zA31OPP2MlCFGuCgH8u
- WINDSOR_API_KEY=3c1bde18818cd2bf5cf52a91fb5bc4634c1a
- Demais variáveis com valores padrão sensatos

### requirements.txt
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
slack-bolt>=1.18.0
anthropic>=0.28.0
httpx>=0.27.0
apscheduler>=3.10.4
aiosqlite>=0.20.0
python-dotenv>=1.0.0
pytz>=2024.1
python-multipart>=0.0.9

## Regras de implementação
1. TODOS os arquivos devem ser Python funcional e completo (não esboços, não TODOs críticos)
2. Async/await ponta a ponta em toda I/O
3. Logging estruturado com logging.getLogger(__name__) em cada módulo
4. Docstring no topo de cada módulo explicando o que exporta
5. Tratamento de exceções sem propagar para o usuário final
6. Nunca hardcode credenciais — sempre via os.getenv()
7. Crie o diretório data/ para o SQLite

## Entregue
Todos os arquivos funcionais e completos. Ao final, rode:
```
cd /workspace/agente-performance && pip install -r requirements.txt --quiet && python -c "import fastapi, slack_bolt, anthropic, httpx, apscheduler, aiosqlite; print('Deps OK')"
```
E confirme que importa sem erros.
assistant: Vou construir o Agente de Performance Guaraná completo. Começando com a estrutura de diretórios e todos os módulos.
assistant: Now let's build all the Python modules. Starting with storage layer:
assistant: Good, directories exist now. Let me write all the files:
assistant: Python isn't installed. Let me use the host exec mode which runs on the host machine (Ubuntu):
assistant: I'll use the `write` tool directly — it bridges to the host filesystem. Let me write all files using the write tool:
assistant: The home directory `~/agente-performance` maps to `/workspace/agente-performance`. I can write files using `tee` with heredocs:
assistant: Now I'll write all files in large batches using bash scripts:
