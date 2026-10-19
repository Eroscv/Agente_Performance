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

## Fact-check e compliance pós-geração

`transcricao/hallucination_guard.py` + `transcricao/compliance_guard.py` (adaptados do par homônimo em `agente-performance/agent/`, mas pro formato JSON estruturado daqui, não texto livre). Chamados por `AnaliseAgent.verificar_guards()` logo após `analisar_com_claude()` retornar:

- `trecho_nao_encontrado`: o `trecho` de algum alerta não aparece no texto real da reunião — fact-check de baixo falso-positivo, porque o próprio `PROMPT_SISTEMA` exige "trecho exato do texto que gerou o alerta".
- `tipo_alerta_invalido`: `tipo` fora dos 5 valores que o `PROMPT_SISTEMA` define.
- `schema_invalido`, `sentimento_invalido`, `blank_response`, `wrong_language`, `prompt_leak` — ver `compliance_guard.py`.

Nunca bloqueia persistência/notificação nem altera o `resultado` — só loga e soma em `resumo["guard_flags"]`, devolvido por `executar()`. Deliberadamente não fact-checa `sentimento` (classificação subjetiva) nem verifica `clientes_mencionados` contra `detectar_clientes()` de `criar_tarefas_notion.py` ainda — candidato a melhoria futura.

## Métricas

Usa o `metricas()` **default** de `BaseSubagente` (não sobrescreve) — lê de `log_subagente`, tabela introduzida junto com `registrar_evento()`. Antes dessa mudança, só sucesso era persistido; agora `executar()` registra "ok" e "erro" por item, o que tornou esta métrica possível por ter dado histórico pra calcular. `guard_flags` (acima) é um contador separado, ainda não alimenta `log_subagente`/IC de Wilson — fica só no resumo em memória de cada execução.

## Como testar

`tests/test_guards.py` (12 testes, zero Claude) cobre os dois guards isoladamente — inclusive o caso de `reuniao` como mapping tipo `sqlite3.Row`, não só dict puro. Comportamento de ponta a ponta (`analisar_com_claude()` em si) ainda exigiria mock do Claude — isso continua sem cobertura.

## Docs relacionados

Notion: "Metodologia aplicada — Subagente de Transcrição" (Camada 2 = Tier 1, "crítico mas reversível").
