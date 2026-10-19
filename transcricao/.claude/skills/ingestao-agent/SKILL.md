---
name: ingestao-agent
description: Use ao trabalhar em IngestaoAgent (Camada 0 do pipeline de Transcrição) — detecção no Google Drive, download de .docx, dedup por fonte_id e gravação tríplice (SQLite + MongoDB + BigQuery). Também use ao investigar por que uma reunião não apareceu no sistema, ou ao mexer em sync_reunioes.py.
---

# IngestaoAgent — Camada 0 (Fundamento)

**Arquivo:** `transcricao/agents/ingestao.py` (classe) → delega para `transcricao/sync_reunioes.py` (implementação real, inalterada).

## Responsabilidade

1. Lista arquivos `.docx` novos na pasta compartilhada do Google Drive (`listar_docx`).
2. Filtra os já processados via `fonte_id` (`ja_processado` — dedup).
3. Baixa cada arquivo novo, delega o parsing à Camada 1 (`ParserAgent`, via composição — `IngestaoAgent(parser=...)` aceita injeção).
4. Grava o resultado em três destinos: SQLite (`reunioes.db`, tabela `reunioes`), MongoDB (dual-write) e BigQuery via **streaming insert** (`insert_rows_json`).

## ⚠️ Risco crítico — falha silenciosa

Diferente de uma resposta errada no Slack (que pelo menos aparece pra alguém), uma falha aqui é **silenciosa e sem segunda chance**: o `fonte_id` é marcado como processado mesmo se os campos vierem vazios, e a reunião só é reprocessada se alguém rodar `--todos` manualmente. Confirmado no código: `ja_processado()` só checa se o ID existe, não se os dados vieram preenchidos.

## ⚠️ BigQuery via streaming insert, não MERGE

Os 4 pontos de escrita deste subagente (`sync_reunioes.py`, `analisador_reunioes.py`, `gestor_alertas.py`, `metricas_alertas.py`) usam `insert_rows_json` — não aparece em `INFORMATION_SCHEMA.JOBS_BY_PROJECT` (só em `STREAMING_TIMELINE_BY_PROJECT`) e tem buffer de ~1min invisível. Se a gravação falhar, nem o histórico de jobs do BigQuery denuncia.

## Métricas

`IngestaoAgent.metricas(conn, janela_dias=90)` sobrescreve o default de `BaseSubagente` — lê de `sync_log` (tabela que já existia antes da estruturação em subagentes, não precisou de `log_subagente`). Retorna taxa de erro com IC de Wilson via `estatistica.taxa_erro()`.

## Como testar

Nenhum teste de comportamento ainda (exigiria mock de Drive/Mongo/BigQuery). Cobertura atual é só estrutural, via `tests/test_agents_estrutura.py` (herança de `BaseSubagente`, composição com `ParserAgent`).

## Credencial exposta (não corrigido)

`transcricao/sync_reunioes.py` tem uma URI do MongoDB com usuário/senha reais hardcoded como default (`MONGO_URI`), e `transcricao/credentials/gdrive-service-account.json` + `oauth_client.json` (chave privada real + client_secret) estão commitados no Git. Rotacionar antes de considerar este subagente "seguro".

## Docs relacionados

Notion: "Metodologia aplicada — Subagente de Transcrição" (chama esta camada de Tier 0 — "não pode falhar de jeito nenhum").
