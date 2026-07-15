# MEMORY.md — Cesar (Agente de Performance Guará)

## Regras Absolutas de Projeto

### 🚫 Flora é INTOCÁVEL
Qualquer trabalho no **Agente de Performance Guará** NUNCA deve afetar:
- Diretórios: `Flora-Agente/`, `Flora-Agente-repo/`, `Flora-Agente-test/`
- Serviços systemd: `flora-agente.service`, `flora-tunnel.service`, `flora-tunnel-watchdog.service`
- Porta 5000 (Flask da Flora)
- Config Apache: `flora-webhook.conf`
- Webhook: `https://44.204.251.251/webhook`

## Projetos Ativos

### Agente de Performance Guará
- **Diretório:** `/home/ubuntu/.openclaw/workspace/agente-performance/`
- **Status:** Em refatoração (2026-06-23) — diretório limpo, apenas `.env` e `.env.example` presentes
- **Stack anterior:** FastAPI + Slack Bolt + APScheduler + SQLite + Windsor API + Anthropic
- **Nome correto:** "Agente de Performance Guará" (não Guaraná)

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
