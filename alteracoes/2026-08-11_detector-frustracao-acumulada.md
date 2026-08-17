# 2026-08-11 — Detector de Frustração Acumulada

## Commit
`724a72a` — feat: detector de frustração acumulada com handoff preventivo para Jana

## Arquivos Alterados
- `agents/orchestrator.py`
- `scripts/weekly_report.py`

## O que foi alterado

### `agents/orchestrator.py`
Adicionado novo bloco de detecção entre o check de agressividade (2) e o encerramento voluntário (3):

**`_FRUSTRATION_RE`** — regex com sinais de frustração implícita:
- Impaciência/repetição: "já perguntei", "quantas vezes", "mesma resposta"
- Decepção: "estou decepcionado/frustrado/insatisfeito/chateado"
- Descaso: "me ignoram", "ninguém me responde", "impossível falar"

**`_has_repeated_intent(history, msg, threshold=6)`** — retorna True se o cliente já enviou ≥6 mensagens substanciais (>5 chars) na sessão, indicando ciclo sem resolução.

**`FRUSTRATION_HANDOFF()`** — mensagem empática: *"Fico feliz que você esteja em contato... vou conectar com nossa equipe agora. 🤎"*

**Check 2b no fluxo principal:**
- Aciona quando: frustração explícita (regex) OU volume ≥6 mensagens
- Motivo registrado: `FRUSTRACAO_EXPLICITA` ou `FRUSTRACAO_ACUMULADA`
- Loga contagem de msgs substanciais para análise retroativa

### `scripts/weekly_report.py`
Adicionados ao `INTENT_LABELS`:
- `FRUSTRACAO_EXPLICITA` → "Frustração Explícita"
- `FRUSTRACAO_ACUMULADA` → "Frustração Acumulada"

## Impacto

| Situação antes | Situação depois |
|---|---|
| Cliente frustrado sem xingamento → Flora continuava tentando responder | Cliente frustrado → handoff preventivo para Jana |
| Ciclo de 6+ mensagens sem resolução → sem escalada automática | 6+ mensagens substanciais + sinal de impaciência → handoff imediato |
| Agressividade era o único trigger de sentimento | Dois níveis: frustração (preventivo) + agressividade (urgente) |

## Lógica de Acionamento
```
2. Agressividade explícita → AGRESSIVIDADE_HANDOFF (imediato)
2b. Frustração:
    - _is_frustrated(msg) → FRUSTRACAO_EXPLICITA
    - _has_repeated_intent(hist, threshold=6) → FRUSTRACAO_ACUMULADA
3. Encerramento voluntário → FAREWELL
```
