---
name: analise-agent
description: Use ao trabalhar em AnaliseAgent (Camada 2 do pipeline de Transcrição) — análise de sentimento, alertas e síntese de reuniões via Claude. Também use ao investigar um alerta classificado errado, ou ao mexer em analisar_com_claude()/analisador_reunioes.py.
---

# AnaliseAgent — Camada 2 (Análise via Claude)

**Arquivo:** `transcricao/agents/analise.py` (classe) → delega para `transcricao/analisador_reunioes.py` (`analisar_com_claude`, `enviar_slack`, `salvar_analise_sqlite`, `salvar_analise_bq`).

## Responsabilidade

Pega reuniões pendentes (`reunioes_pendentes`), chama o Claude pra extrair sentimento + alertas + síntese, persiste em `analise_reunioes` (SQLite + BigQuery) e notifica no Slack. Primeiro ponto do pipeline que usa LLM — antes dele, tudo é determinístico (regex).

## ⚠️ Disciplina de diagnóstico (Metodologia Parte A)

Antes de tocar no prompt do Claude aqui quando um alerta sai estranho, **checar primeiro se `resumo`/`decisoes`/`proximas_etapas` chegaram preenchidos no banco pra aquela reunião** (ver skill `parser-agent`). Se vieram vazios, o conserto é no `ParserAgent` (Camada 1), nunca no prompt de análise — mexer aqui seria tratar o sintoma, não a causa.

## Fact-check pós-geração — ainda não implementado aqui

O Agente de Performance (`agente-performance/agent/hallucination_guard.py` + `compliance_guard.py`) já tem guards pós-geração que verificam a resposta do Claude contra os dados antes de confiar nela. Este subagente **ainda não tem equivalente** — `analisar_com_claude()` confia no JSON que o Claude devolve sem verificação adicional. Candidato natural pra portar esses guards (ex: `cliente_cruzado` já existe como conceito em `detectar_clientes`; faltaria um `periodo_fora_do_escopo` ou verificação de que o `sentimento`/`alertas` são consistentes com o texto da reunião).

## Métricas

Usa o `metricas()` **default** de `BaseSubagente` (não sobrescreve) — lê de `log_subagente`, tabela nova introduzida junto com `registrar_evento()`. Antes desta mudança, só sucesso era persistido; agora `executar()` registra "ok" e "erro" por item, o que tornou esta métrica possível por ter dado histórico pra calcular.

## Como testar

Só cobertura estrutural hoje (`tests/test_agents_estrutura.py`). Comportamento real exigiria mock do Claude (`agent.claude_client`-equivalente aqui seria mockar `_call_llm` ou a chamada ao Claude dentro de `analisar_com_claude`).

## Docs relacionados

Notion: "Metodologia aplicada — Subagente de Transcrição" (Camada 2 = Tier 1, "crítico mas reversível").
