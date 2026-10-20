---
name: parser-agent
description: Use ao trabalhar em ParserAgent (Camada 1 do pipeline de Transcrição) — extrai resumo/decisões/próximas etapas/participantes do texto bruto do .docx exportado pelo Gemini Notes. Também use ao investigar campos vazios numa reunião processada, ou ao mexer em parsear_reuniao()/sync_reunioes.py.
---

# ParserAgent — Camada 1 (Interpretação)

**Arquivo:** `transcricao/agents/parser.py` (classe, fina) → delega para `parsear_reuniao()`/`extrair_texto()` em `transcricao/sync_reunioes.py` (zero lógica nova no wrapper).

## Responsabilidade

Função pura: `(texto: str, nome_arquivo: str) -> dict`. Zero I/O, zero LLM, zero Drive — só regex sobre texto já extraído do `.docx`. Extrai: `data`, `hora`, `titulo`, `participantes`, `resumo`, `decisoes`, `proximas_etapas`, `detalhes`.

Por ser a camada mais fundamental e mais barata de testar, é a que a Metodologia aplicada (Notion) recomenda testar primeiro — "qual skill testar primeiro: `parsear_reuniao()`".

## ⚠️ Known gaps confirmados (documentados em `tests/test_parsear_reuniao.py`, `xfail(strict=True)`)

1. **Cabeçalho alternativo**: o parser procura a string literal `"Próximas etapas"`. Se o Gemini Notes variar pra `"Próximos passos"`, o campo sai vazio **e** a seção `"Decisões"` anterior fica contaminada com o conteúdo da seção seguinte (o delimitador de parada em `_entre_secoes()` não bate).
2. **Participantes com prefixo sobrando**: `linha.replace("convidado", "")` é case-sensitive, mas a checagem de prefixo (`linha.lower().startswith(...)`) não é — com cabeçalho capitalizado (`"Convidado ..."`, como o Gemini Notes normalmente exporta), a palavra "Convidado" sobra no campo.

Nenhum dos dois foi corrigido — por metodologia, o fix só entra depois de confirmado volume/criticidade suficiente (via `metricas()`, ver abaixo), não como reação ao primeiro caso visto.

## Métricas

`ParserAgent.metricas(conn, janela_dias=90)` sobrescreve o default — como `parsear_reuniao()` nunca lança exceção, "erro" aqui é **qualidade de extração**: % de reuniões no período com algum campo-chave (`resumo`/`decisoes`/`proximas_etapas`) vazio, lido direto da tabela `reunioes`.

## Como testar

`tests/test_parsear_reuniao.py` — fixtures de texto puro em `tests/fixtures/*.txt` (simulam a saída de `extrair_texto()`, não precisam de `.docx` real nem python-docx). Padrão: um caso feliz + um caso por known gap, cada known gap com `xfail(strict=True)` e comentário explicando a causa raiz.

## Docs relacionados

Notion: "Metodologia aplicada — Subagente de Transcrição" (seção "As 5 perguntas, aplicadas" — item 1, a "mão fraca" deste subagente).
