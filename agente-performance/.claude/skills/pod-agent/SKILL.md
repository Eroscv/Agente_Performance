---
name: pod-agent
description: Use ao trabalhar em PODAgent — subagente de cliente do Agente de Performance para POD Kombucha, que NUNCA deve injetar dados Shopify. Também use ao investigar vazamento de dado Shopify pra esse cliente, ou ao mexer em agent/clients/pod.py/clientes/pod/prompts.py.
---

# PODAgent — Subagente de cliente (consulta)

**Arquivo:** `agent/clients/pod.py` (herda `BaseClientAgent`). `CLIENTE_KEY = "pod_kombucha"`. `PLATAFORMA_PADRAO = "meta_ads"`.

## ⚠️ Regra permanente — a mais crítica deste subagente

POD **NUNCA** injeta dados Shopify. Confirmado: sem `_enriquecer()` sobrescrito, sem nenhuma referência a Shopify no arquivo. A regra é tão central que o docstring do arquivo a lista como primeira linha. Isso é o caso de teste #1 documentado na Metodologia de Teste (seção 2.2):

```python
def test_pod_nunca_recebe_shopify(fixture_db, mock_llm):
    agent = PODAgent()
    resultado = agent.execute({"intent": "resumo_geral", ...})
    assert "shopify" not in resultado.get("meta", {})
    # mesmo que exista receita Shopify na fixture para outro cliente
```
Esse teste ainda **não existe** no repositório — é a maior lacuna de risco de vazamento de dado entre clientes do Agente de Performance.

## Particularidade

Meta Ads é a plataforma ativa (`PLATAFORMA_PADRAO = "meta_ads"`); Google Ads existe no `clientes.json` mas com `ativo=false` — confirmar status atual antes de assumir que está desligado permanentemente.

## Fact-check / Compliance pós-geração

A checagem `cliente_cruzado` em `agent/hallucination_guard.py` é o guard mais relevante aqui: se uma resposta sobre POD mencionar "Source OF" (incluindo, por simetria, qualquer menção indevida a dado Shopify que só existe pra Source OF), a confiança pós-guards cai pra `medium`/`low` (penalidade −0.50).

## Como testar

`tests/clients/test_pod_agent.py` (não implementado ainda) é o teste de contrato prioritário — ver trecho de código acima, já desenhado na Metodologia de Teste. `tests/fixture_db` (conftest.py) já tem dados sintéticos de POD (2 campanhas Meta + 1 Google) prontos pra alimentar esse teste quando ele for escrito.

## Docs relacionados

Notion: "Agente de Performance Guará — Arquitetura e Subagentes" (seção 4 — tabela do factory), "Metodologia de Teste de Skills" (seção 2.2, exemplo de código literal pra este teste).
