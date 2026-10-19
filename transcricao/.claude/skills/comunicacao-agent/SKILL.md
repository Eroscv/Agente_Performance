---
name: comunicacao-agent
description: Use ao trabalhar em ComunicacaoAgent (Camada 4 do pipeline de Transcrição) — cria cards de tarefa no Notion a partir das próximas etapas de uma reunião. Também use ao investigar card duplicado/faltante no Notion, ou ao mexer em criar_tarefas_notion.py.
---

# ComunicacaoAgent — Camada 4 (Comunicação)

**Arquivo:** `transcricao/agents/comunicacao.py` (classe) → delega para `transcricao/criar_tarefas_notion.py`.

## Responsabilidade

1. Busca reuniões com `proximas_etapas` preenchido (`_buscar_reunioes`).
2. Extrai tarefas por responsável (`extrair_tarefas` → `parsear_tarefas`, regex `[Nome] Título: Descrição`).
3. Detecta cliente/área (`detectar_contexto`).
4. Cria um card no Notion por tarefa (`criar_card`), com dedup por reunião.

## ⚠️ Bug de dedup — já corrigido nesta sessão, mas entenda o histórico

`tarefas_ja_criadas()` existia mas nunca era chamada, **e mesmo chamando, sempre voltaria vazia**: o filtro busca a propriedade `"Origem Reunião"` no Notion, mas `criar_card_notion()` nunca escrevia essa propriedade. Os dois lados foram corrigidos juntos (`criar_card_notion()` agora grava `fonte_id` em `"Origem Reunião"`; `executar()` consulta o dedup uma vez por reunião). Se a dedup parecer não funcionar de novo, confira os dois lados — corrigir só um não resolve nada, como já aconteceu antes.

## ⚠️ Credencial exposta (não corrigido)

`NOTION_KEY` em `criar_tarefas_notion.py` tem uma chave real da API do Notion hardcoded como *default* (`os.getenv("NOTION_KEY", "ntn_...")`). Rotacionar junto com as outras credenciais expostas deste subagente (Mongo, Google Drive service account).

## Dependência de dados

Depende só da Camada 0/1 (`reunioes.proximas_etapas`) — **independente** de Análise/Ticket (Camadas 2/3). Por isso precisa estar sempre no pipeline (`orquestrador.py`), mesmo que a análise ou os tickets falhem naquele ciclo. `run_sync.sh` (versão antiga, bash) esquecia esta camada inteira — o orquestrador novo corrige isso.

## Métricas

Usa o default de `BaseSubagente` — `registrar_evento()` chamado por card criado/falhado, lido de `log_subagente`.

## Como testar

`tests/test_comunicacao_dedup.py` — mocks em `criar_tarefas_notion.tarefas_ja_criadas`/`criar_card_notion`/`requests.post`, SQLite temporário via `monkeypatch`. Nenhuma chamada real ao Notion.

## Docs relacionados

Notion: "Metodologia aplicada — Subagente de Transcrição" (Camada 4 = Tier 2, "acabamento — como o alerta é escrito").
