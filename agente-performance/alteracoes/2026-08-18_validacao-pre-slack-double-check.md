# Alteração: Validação obrigatória pré-Slack com double-check

**Commit:** `a2b0a8f`  
**Mensagem:** feat(relatorio): validação obrigatória pré-Slack com double-check  
**Data:** 2026-08-18

## Arquivos Alterados

- `scripts/cron_todos_relatorios.py`

## O que foi alterado

Implementada etapa de validação obrigatória entre a geração do relatório e o envio ao Slack.

### Fluxo obrigatório (agora garantido em código)

```
Buscar dados → Validar dados → Gerar relatórios → Validar relatórios
→ Montar mensagem → Double check → Enviar Slack → Confirmar envio
```

### Checklist de validação (bloqueia envio se qualquer item falhar)

| Check | Campo validado | Falha se |
|-------|----------------|----------|
| Dados existem | `resultado["resumo"]` | vazio ou ausente |
| Investimento presente | `resumo["investimento"]` | zerado ou ausente |
| Título gerado | `resultado["titulo"]` | ausente ou vazio |
| Conteúdo gerado | `resultado["blocos"]` | lista vazia |
| Período identificado | `resultado["data"]` ou `data_inicio` | ausente |
| Notion publicado | `resultado["url"]` | ausente quando `publicar=True` |

### Logs diferenciados

- `✅ Validação pré-Slack OK` → dados válidos, segue para envio
- `❌ VALIDAÇÃO PRÉ-SLACK FALHOU — bloqueando envio` → levanta `RuntimeError` com motivos
- `✅ Slack: mensagem enviada com sucesso` → confirmação pós-envio
- `❌ Slack: envio falhou após tentativas` → alerta de falha de entrega

## Impacto

| Cenário | Antes | Depois |
|---------|-------|--------|
| Dados vazios chegam ao builder | Enviava mensagem vazia ao Slack | ❌ Bloqueado antes do envio |
| Investimento zero/null | Mensagem incompleta no Slack | ❌ Bloqueado |
| Notion não publicou (URL ausente) | Enviava sem link | ❌ Bloqueado |
| Relatório válido | Enviava (sem confirmação explícita) | ✅ Valida + confirma envio |
| Dados inválidos | Sem rastreabilidade | Motivos detalhados no log |
