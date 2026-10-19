---
name: metricas-agent
description: Use ao trabalhar em MetricasAgent (Camada 3 do pipeline de Transcrição) — métricas de SLA de tickets com Intervalo de Confiança de Wilson. Também use ao decidir se uma taxa de erro/resolução é estatisticamente significativa, ou ao mexer em ic_wilson()/metricas_alertas.py/estatistica.py.
---

# MetricasAgent — Camada 3 (Regra de negócio: métricas/SLA)

**Arquivo:** `transcricao/agents/metricas.py` (classe) → delega para `transcricao/metricas_alertas.py` (`coletar_metricas`, `formatar_relatorio`, `salvar_metricas_bq`, `enviar_slack`) e `transcricao/estatistica.py` (`ic_wilson`, `taxa_erro`).

## Por que esta é a camada "mais madura" do ecossistema (Notion)

`ic_wilson()` usa Intervalo de Confiança de Wilson em vez de taxa bruta, porque alertas urgentes são eventos raros (poucos por mês) — uma taxa de "90% de resolução" com n=5 não significa nada estatisticamente. Essa técnica foi **extraída** pra `estatistica.py` (zero dependências) e **reutilizada** pelos outros 5 subagentes via `BaseSubagente.metricas()`/`estatistica.taxa_erro()` — não o contrário. Se for alterar a fórmula de IC aqui, o impacto se propaga pra todos os outros subagentes.

## Responsabilidade

- `coletar()`: taxa de resolução de tickets por tipo de alerta (`urgente`, `sem_resposta`, `bloqueio`, `sem_acesso`, `conflito`) + SLA vencidos + tendência semanal.
- `formatar()`: texto pronto pra Slack.
- `publicar_bq()` / `publicar_slack()`: side-effects opcionais, controlados por flag (`--bq`/`--slack` no CLI).

## Avaliar o avaliador

`MetricasAgent.metricas()` usa o default de `BaseSubagente` (lê `log_subagente` filtrado por `CAMADA`) — hoje roda zerado (`n=0`), porque nada escreve eventos com essa `CAMADA` ainda. "Avaliar a camada de métricas" é um problema ainda sem resposta clara (circular por natureza) — ver `avaliar_subagentes.py`.

## Como testar

Zero testes de comportamento hoje. `ic_wilson()`/`taxa_erro()` são funções puras (`estatistica.py`) — as mais baratas de testar isoladamente no pipeline inteiro, candidatas naturais a um `test_estatistica.py` com casos conhecidos (n pequeno vs. grande, k=0, k=n).

## Docs relacionados

Notion: "Metodologia aplicada — Subagente de Transcrição" (cita `ic_wilson()` como o ponto mais maduro, recomendando levar de volta pro Agente de Performance — já feito, ver `agente-performance/agent/confidence.py::recalcular_pos_guards()`).
