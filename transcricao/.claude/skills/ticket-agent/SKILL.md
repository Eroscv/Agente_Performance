---
name: ticket-agent
description: Use ao trabalhar em TicketAgent (Camada 3 do pipeline de Transcrição) — abre e resolve tickets a partir de alertas críticos detectados pela análise. Também use ao investigar SLA de tickets ou ao mexer em gestor_alertas.py.
---

# TicketAgent — Camada 3 (Regra de negócio: tickets/SLA)

**Arquivo:** `transcricao/agents/ticket.py` (classe) → delega para `transcricao/gestor_alertas.py`.

## Responsabilidade

1. `abrir_tickets_pendentes()`: lê `analise_reunioes` onde `alertas != '[]'` e ainda não tem ticket, abre um ticket por alerta crítico (`abrir_tickets_da_analise`).
2. `processar_resolucoes()`: verifica, via Claude, se reuniões posteriores mencionam a resolução de um ticket aberto (`verificar_resolucao_claude`).
3. `fechar_manual()` / `status()`: operações administrativas, usadas pelo CLI de `gestor_alertas.py --fechar`/`--status`.

## Dependência de dados

Depende do `AnaliseAgent` (Camada 2) já ter gravado a análise em `analise_reunioes` — se a Camada 2 estiver com problema (ver skill `analise-agent`), este subagente processa alertas vazios ou desatualizados sem saber que a causa é upstream.

## Métricas — delega, não reimplementa

`TicketAgent.metricas(conn, janela_dias=90)` **não calcula nada por conta própria** — chama `MetricasAgent().coletar(conn, janela_dias=janela_dias)` direto, porque a avaliação desta camada já É `coletar_metricas()` (taxa de resolução por tipo de alerta + IC de Wilson + SLA vencidos). Ver skill `metricas-agent` para o que isso retorna.

## Como testar

Só cobertura estrutural hoje. Comportamento real (abrir ticket, verificar resolução) exigiria fixture de `analise_reunioes`/`tickets_alerta` + mock do Claude em `verificar_resolucao_claude`.

## Docs relacionados

Notion: "Metodologia aplicada — Subagente de Transcrição" (Camada 3 = Tier 1).
