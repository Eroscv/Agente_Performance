---
name: general-agent
description: Use ao trabalhar em GeneralAgent — subagente do Agente de Performance para perguntas sem cliente identificado ou genuinamente multi-cliente ("compare POD e Source OF"). Também use ao investigar agregação de segmentos ou ao mexer em agent/clients/general.py.
---

# GeneralAgent — Subagente multi-cliente (fallback)

**Arquivo:** `agent/clients/general.py` (herda `BaseClientAgent`). `CLIENTE_KEY = ""` (vazio — não representa um cliente específico). `PLATAFORMA_PADRAO = "meta_ads"`.

## Quando é usado

Prioridade de detecção de cliente no `OrchestratorAgent` é **NLP > canal Slack mapeado > nenhum** — `GeneralAgent` é o "nenhum": nunca assume Source OF por padrão.
1. NLP (`agent/nlp.py::interpretar()`) não detectou `cliente_key`.
2. Canal Slack sem mapeamento em `clientes.json`.
3. Pergunta explicitamente multi-cliente (ex: "compare POD e Source OF").

## Comportamento — único subagente que roda em paralelo

`execute()` sobrescreve o fluxo padrão de `BaseClientAgent`: executa `execute_for_segment()` **para cada segmento ativo** (via `agent/registry.py::carregar_segmentos_ativos()`, que lê `clientes.json`) e agrega as respostas em seções separadas por cliente — não delega pra um único `ClientAgent`.

## Risco diferente dos outros 3 subagentes

Aqui o risco de "cliente_cruzado" (ver `agent/hallucination_guard.py`) é estruturalmente diferente: **é esperado e correto** que a resposta mencione múltiplos clientes (ex: "compare os clientes"). A checagem `cliente_cruzado` do guard de alucinação já lida com isso corretamente — ela só é acionada quando `meta.get("cliente")` é um valor conhecido (`source_of`/`pod_kombucha`/`botica`); quando é `None`/vazio (como neste subagente), a checagem é pulada de propósito (ver `test_sem_cliente_cruzado_quando_cliente_pedido_e_none` em `tests/test_guards.py`).

## Como testar

`tests/clients/test_general_agent.py` (não implementado):
```python
def test_general_agrega_todos_segmentos_ativos(fixture_db, mock_llm):
    resultado = GeneralAgent().execute({"pergunta": "compare os clientes"})
    assert len(resultado["dados"]) == len(carregar_segmentos_ativos())
```
`tests/fixture_db` já tem 3 clientes com dados sintéticos (Source OF, POD, Botica) prontos pra esse teste.

## Docs relacionados

Notion: "Agente de Performance Guará — Arquitetura e Subagentes" (seção 3 — prioridade de detecção; seção 4 — tabela do factory).
