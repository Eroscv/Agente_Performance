---
name: botica-agent
description: Use ao trabalhar em BoticaAgent — subagente de cliente do Agente de Performance para Botica Ouro Preto, só Meta Ads (Google Ads não configurado). Também use ao investigar os known_gap de cliente Botica no corpus de NLP, ou ao mexer em agent/clients/botica.py/clientes/botica/prompts.py.
---

# BoticaAgent — Subagente de cliente (consulta)

**Arquivo:** `agent/clients/botica.py` (herda `BaseClientAgent`). `CLIENTE_KEY = "botica"`. `PLATAFORMA_PADRAO = "meta_ads"`.

## ⚠️ Docstring desatualizado (achado, não corrigido)

O docstring do arquivo diz "Plataformas ativas: Meta Ads ✅ | Shopify ✅", mas isso é falso: não há nenhum código relacionado a Shopify neste arquivo (confirmado via grep), e a arquitetura geral documenta Source OF como **único** cliente com Shopify. Antes de confiar no docstring, confirmar no código — e, se for corrigi-lo, só remover o "| Shopify ✅", não adicionar lógica de Shopify (isso violaria a regra de exclusividade da `source-of-agent` skill).

## Particularidade real

Google Ads **não está configurado** para este cliente ainda (confirmado: `PLATAFORMA_PADRAO = "meta_ads"`, sem alternativa). Perguntas sobre "Botica no Google" devem cair num fallback claro, não um erro silencioso.

## Known gap de NLP relacionado

O corpus de avaliação de NLP (`agente-performance/tests/fixtures/nlp_corpus.json`) tem 3 casos `known_gap` especificamente sobre detecção de `cliente_key=botica` (`gap_botica_01/02/03`) — confirmados como falha esperada em toda rodada de `scripts/avaliar_nlp.py`, com ou sem API key real. Antes de investigar um problema de roteamento pra Botica, checar se já é um desses gaps conhecidos.

## Relatório Notion — ainda não configurado

Diferente de Source OF e POD (que compartilham um database no Notion), Botica **não tem relatório Notion configurado** (confirmado na arquitetura geral). `BaseRelatorioAgent`/`get_relatorio_agent("botica")` provavelmente falha ou não existe — confirmar antes de tentar `/relatorio botica` via Slack.

## Como testar

`tests/clients/test_factory.py` (não implementado) deveria parametrizar `("botica", BoticaAgent)` entre os casos do `get_client_agent()`. Nenhum teste de contrato específico de Botica existe hoje.

## Docs relacionados

Notion: "Agente de Performance Guará — Arquitetura e Subagentes" (seção 4, 9), "Metodologia de Teste de Skills" (seção 3 — matriz intents×clientes, linha "comparar_plataformas: ⚠️ só Meta").
