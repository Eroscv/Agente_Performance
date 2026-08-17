# 2026-08-11 — Melhorias de Coleta e Salvamento de Dados no SQLite

## Commit
`209104f` — fix: melhorias de coleta e salvamento de dados no SQLite

## Arquivos Alterados
- `memory/shared_memory.py`
- `agents/orchestrator.py`
- `webhook.py`
- `buffer/buffer_manager.py`

## O que foi corrigido / melhorado

### 1. Timestamps UTC consistentes (`shared_memory.py`)
**Problema:** `datetime.utcnow()` retorna objeto naive (sem timezone), causando inconsistência com `datetime.now(_tz.utc)` usado em `add_message`.

**Correção:**
- Adicionados helpers `_now_iso()` e `_now_ts()` no topo do módulo
- `_now_iso()` → `datetime.now(_tz.utc).isoformat()` (ex: `2026-08-11T14:30:00.123456+00:00`)
- `_now_ts()` → `datetime.now(_tz.utc).timestamp()` (epoch float)
- Substituídas **todas** as chamadas `datetime.utcnow()` pelos novos helpers

### 2. Tabela `satisfaction` criada no `_init_db`
**Problema:** Tabela referenciada em migrations e em `register_satisfaction()`, mas nunca criada com `CREATE TABLE IF NOT EXISTS`. Qualquer chamada de satisfação falhava silenciosamente.

**Correção:** Tabela criada com colunas `id, phone, score, label, intent, created_at, created_at_ts`.

### 3. `register_handoff` e `register_satisfaction` salvam `created_at_ts`
**Problema:** Coluna `created_at_ts` existia via migration mas nunca era preenchida na inserção — ficava NULL.

**Correção:** Ambos os métodos agora passam `_now_ts()` no INSERT.

### 4. Propagação de `wamid` (dedup por ID Meta)
**Problema:** O ID único da mensagem WhatsApp (`wamid`) chegava no payload Meta mas era descartado — sem dedup a nível de mensagem.

**Fluxo corrigido:**
```
Meta webhook → webhook.py extrai msg['id']
  → buffer.add_message(wamid=msg_id)
    → buffer._flush passa primeiro wamid ao callback
      → on_message_ready(wamid=wamid)
        → orchestrator.handle(wamid=wamid)
          → memory.add_message(wamid=wamid)
            → dedup por UNIQUE INDEX idx_messages_wamid
```

**Arquivos modificados:** `webhook.py`, `buffer/buffer_manager.py`, `agents/orchestrator.py`, `memory/shared_memory.py`

## Impacto

| Antes | Depois |
|---|---|
| Timestamps sem timezone (naive) em 10+ métodos | Todos os timestamps com UTC explícito |
| Tabela `satisfaction` ausente → falhas silenciosas | Tabela criada corretamente |
| `handoffs.created_at_ts` sempre NULL | Salvo corretamente no INSERT |
| Mensagens duplicadas (retry Meta) salvas 2x | Dedup automático por `wamid` |
