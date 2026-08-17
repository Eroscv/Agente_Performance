# MEMORY.md — Cesar (Agente de Performance Guará)

## Regras Absolutas de Projeto

### 🚫 Flora é INTOCÁVEL
Qualquer trabalho no **Agente de Performance Guará** NUNCA deve afetar:
- Diretórios: `Flora-Agente/`, `Flora-Agente-repo/`, `Flora-Agente-test/`
- Serviços systemd: `flora-agente.service`, `flora-tunnel.service`, `flora-tunnel-watchdog.service`
- Porta 5000 (Flask da Flora)
- Config Apache: `flora-webhook.conf`
- Webhook: `https://44.204.251.251/webhook`

## Regras de Workflow

### 📝 Após cada commit + push no Flora-Agente
Criar arquivo em `alteracoes/YYYY-MM-DD_<slug>.md` com:
- **Commit** (hash + mensagem)
- **Arquivos alterados**
- **O que foi alterado** (descrição objetiva)
- **Impacto** (tabela antes/depois)

Depois commitar e pushar o arquivo de alteração junto ou em seguida.

## Projetos Ativos

### Agente de Performance Guará
- **Diretório:** `/home/ubuntu/.openclaw/workspace/agente-performance/`
- **Status:** Produção ativa (porta 8001, `agente-guara.service`)
- **Stack:** FastAPI + Slack Bolt + SQLite + Claude Haiku 4.5 + Notion API
- **Clientes:** Source OF (Meta Ads) + POD Kombucha (Google Ads) + 4 outros
- **Shopify Sync:** `db/shopify_sync.py` — tabela `shopify_receita_diaria` — 387 dias, 31.285 pedidos, R$ 9,58M sincronizados
  - ROAS real calculado: Receita Shopify ÷ Investimento Meta ajustado (×1,1215)
  - Cron automático: `shopify-sync-diario` às 6h UTC (últimos 8 dias)
  - Endpoints: GET /shopify/status · POST /shopify/sync · GET /shopify/receita · GET /shopify/diario

### Flora-Agente
- **Diretório:** `/home/ubuntu/.openclaw/workspace/Flora-Agente-repo/`
- **Número do agente (WhatsApp):** `554198980030` (+55 41 99898-0030)
- **Serviço:** `flora-agente.service` (porta 5000)
- **Webhook:** `https://44.204.251.251/webhook` via Apache (cert snakeoil)
- **Tunnel:** Cloudflare via `flora-tunnel.service` + `flora-tunnel-watchdog.service`
- **Whitelist:** `ALLOWED_PHONES=5519995994481` (apenas Eros)
- **Modelo:** `claude-haiku-4-5`; `ProdutoAgent` usa `claude-sonnet-4-5`

## Infraestrutura

### Servidor
- IP: `44.204.251.251`
- OS: Ubuntu, Apache 2.4.58
- OpenClaw rodando em loopback (`127.0.0.1:18789`)

### Apache
- `flora-webhook.conf` — proxy para Flask (porta 5000): `/webhook`, `/health`, `/tunnel-url`
- `000-default.conf` — redireciona HTTP → HTTPS

### GitHub
- Token: `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`
- Repositório Flora: https://github.com/Eroscv/Flora-Agente (privado, owner: Eroscv)
