# Alteração: 2026-08-11 — Fix modelo Gemini deprecado

## Commit
- Hash: `16c4e98`
- Mensagem: `fix: atualiza GEMINI_MODEL para gemini-2.0-flash (2.5-flash deprecado)`
- Repo: https://github.com/Eroscv/Flora-Agente

## Arquivos alterados
- `config/settings.py`
- `.env` (não versionado — alterado localmente)

## O que foi alterado
O modelo `gemini-2.5-flash` foi deprecado pelo Google e retornava `404 NOT_FOUND` em todas as requisições, forçando fallback para Claude em 100% das chamadas. Atualizado para `gemini-2.0-flash` que está ativo e disponível.

## Impacto

| Antes | Depois |
|---|---|
| Gemini 2.5 Flash → 404 → fallback Claude | Gemini 2.0 Flash → resposta normal |
| Latência dupla (tenta Gemini + espera timeout antes do fallback) | Latência normal (primeira chamada resolve) |
| Logs cheios de erros 404 repetidos | Sem erros de modelo |
| Custo elevado (overhead de chamadas falhas) | Custo normalizado |

## Serviço
`flora-agente.service` reiniciado com sucesso às 12:39 UTC.
