---
name: source-of-agent
description: Use ao trabalhar em SourceOFAgent — subagente de cliente do Agente de Performance, único com Shopify integrado (receita real, ROAS real, CAC, taxa de recompra). Também use ao investigar enriquecimento Shopify ou ao mexer em agent/clients/source_of.py/clientes/source_of/prompts.py.
---

# SourceOFAgent — Subagente de cliente (consulta)

**Arquivo:** `agent/clients/source_of.py` (herda `BaseClientAgent`). `CLIENTE_KEY = "source_of"`. `PLATAFORMA_PADRAO = None` (sem filtro — Meta Ads + Google Ads combinados por padrão).

## ⚠️ Regra permanente

Source OF é **o único cliente com Shopify integrado** — Meta Ads ✅ + Google Ads ✅ + Shopify ✅. Essa exclusividade está documentada no próprio docstring do arquivo e na arquitetura geral (Notion). Qualquer enriquecimento de receita real/ROAS real/CAC/taxa de recompra só deveria disparar aqui.

**Contradição encontrada no código, não corrigida**: `agent/clients/botica.py` tem no docstring "Plataformas ativas: Meta Ads ✅ | Shopify ✅", mas o arquivo não tem nenhuma linha de código relacionada a Shopify (confirmado via grep) — é docstring desatualizado, não uma segunda exceção à regra. Se alguém usar esse docstring como referência, vai assumir Botica tem Shopify quando não tem.

## Enriquecimento

`_enriquecer()` injeta receita real, ROAS real (Shopify ÷ Meta ajustado), CAC e taxa de recompra quando `shopify.sincronizado=True`. Ver `agent/analysis_agent.py::enrich_shopify` para onde essa injeção acontece antes de chegar no `ClientAgent`.

## Prompts

`clientes/source_of/prompts.py::SYSTEM_PROMPT` — específico pro cliente, cobre Meta Ads + Google Ads + Shopify. Também tem `clientes/source_of/dados_historicos.py` e `relatorio/` próprio.

## Fact-check / Compliance pós-geração

`agent/claude_client.py::gerar_resposta()` roda `hallucination_guard.verificar_alucinacao()` e `compliance_guard.verificar_compliance()` no texto final — a checagem `cliente_cruzado` é especialmente relevante aqui: se a resposta sobre Source OF mencionar "POD" ou "Botica", é flagrado e a confiança cai. Ver `agent/confidence.py::recalcular_pos_guards()`.

## Como testar

Contrato de subagente (`tests/clients/test_source_of_agent.py` — ainda não implementado, é a maior lacuna de teste do Agente de Performance segundo a Metodologia de Teste, seção 2.2). Hoje a cobertura real depende de `tests/test_engine_validacao.py` (caminho legado) e `tests/test_fixture_db_demo.py` (fixture sintética, camada de query, não o `ClientAgent` em si).

## Docs relacionados

Notion: "Agente de Performance Guará — Arquitetura e Subagentes" (seção 4), "Metodologia de Teste de Skills" (seção 2.2 e 3 — matriz intents×clientes).
