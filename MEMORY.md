# MEMORY.md — Cesar (Agente de Performance Guará)

## Regras Globais de Comunicação

### 🕐 Timezone — REGRA FIXA
- **Sempre exibir horários em BRT (UTC-3)** em qualquer projeto, resposta ou log ao usuário
- Nunca exibir UTC sem converter, a menos que seja log interno de sistema

---

## Regras de Qualidade — Flora-Agente

### ✅ Checagem Obrigatória em Alterações de Prompt
Sempre que qualquer prompt da Flora for alterado (especialmente `_produto.py`, `_vendas.py`, `_logistica.py`, `_pagamento.py`, `_assinatura.py`, `_geral.py`):
1. **Rodar checagem de evidência** antes de commitar — verificar que cada informação adicionada está presente no arquivo
2. **Validar ausência** de texto antigo/conflitante removido
3. **Confirmar consistência** entre regras anti-alucinação, fichas técnicas e FAQ
4. Só commitar após **100% dos checks passando**

### 📋 Checklist de Qualidade para Fichas de Produto
Cada produto deve ter documentado:
- [ ] Descrição geral
- [ ] Ingredientes-chave com função
- [ ] Benefícios principais
- [ ] Modo de uso
- [ ] Resultados clínicos (se existirem)
- [ ] Fragrância (ou ausência)
- [ ] Observação sobre LILIAL se aplicável
- [ ] Vegano / Sem parabenos / Tipo de pele

---

## Regras Absolutas de Projeto

### 🚫 Flora é INTOCÁVEL
Qualquer trabalho no **Agente de Performance Guará** NUNCA deve afetar:
- Diretórios: `Flora-Agente/`, `Flora-Agente-repo/`, `Flora-Agente-test/`
- Serviços systemd: `flora-agente.service`, `flora-tunnel.service`, `flora-tunnel-watchdog.service`
- Porta 5000 (Flask da Flora)
- Config Apache: `flora-webhook.conf`
- Webhook: `https://44.204.251.251/webhook`

## Flora — Valores Fixos do Relatório Anual 2026

> ⚠️ Esses valores são FIXOS. Sempre que gerar qualquer relatório anual ou de "todos os meses" da Flora, usar exatamente esses números — nunca recalcular, nunca substituir.

| Mês | Total | Reais | ✅ Flora | 🔁 Redir. | 💬 Casual | Taxa |
|---|---|---|---|---|---|---|
| Janeiro | 387 | 329 | 284 | 49 | 58 | 86% |
| Fevereiro | 820 | 697 | 601 | 105 | 123 | 86% |
| Março | 756 | 643 | 555 | 96 | 113 | 86% |
| Abril | 1.158 | 984 | 848 | 148 | 174 | 86% |
| Maio | 724 | 615 | 531 | 92 | 109 | 86% |
| Junho | 428 | 275 | 135 | 140 | 153 | 49% |
| Julho | 457 | 427 | 302 | 125 | 30 | 71% |
| Agosto | 541 | 360 | 243 | 117 | 181 | 68% |
| **TOTAL** | **5.271** | **4.330** | **3.499** | **872** | **941** | **81%** |

- Página Notion de referência: `https://notion.so/3cd8a727a8ff814f8e2eebcee13d753f`
- Critério: Resolvido Flora / Redirecionado / Casual (excluída dos reais)
- Jan–Mai: estimativa histórica. Jun–Ago: IA + ajuste manual aprovado pelo usuário.

---

## Regras de Workflow

### 📝 Após cada commit + push no Flora-Agente
Criar arquivo em `alteracoes/YYYY-MM-DD_<slug>.md` com:
- **Commit** (hash + mensagem)
- **Arquivos alterados**
- **O que foi alterado** (descrição objetiva)
- **Impacto** (tabela antes/depois)

Depois commitar e pushar o arquivo de alteração junto ou em seguida.

## Projeto: reportana_api_shopify

### Regras de Tamanho de Segmento (Reportana — Source OF)
- **RPTN · New** (ID 93398): mín 700 → máx 1.000 por carga. Crescimento **gradual** a cada ciclo — nunca ultrapassar 1.000 de uma vez.
- **RPTN · Loyal** (ID 93397): entre **80 e 200** por carga.
- **RPTN · Champions** (ID 93396): entre **80 e 200** por carga.
- Serviço: `rptn-sync.service` (systemd) — diretório `/home/ubuntu/.openclaw/workspace/reportana_api_shopify/`
- Estado em 22/09/2026: New=9.132 | Loyal=3.365 | Champions=1.256 (banco acumulado total, não por carga)
- Serviço ficou parado 6 dias (09/09–22/09) por SIGTERM manual; reativado em 22/09.

---

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
