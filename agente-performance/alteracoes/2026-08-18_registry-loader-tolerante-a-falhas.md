# Alteração: registry.py — Loader composto tolerante a falhas

**Commit:** `418406f`  
**Mensagem:** fix(registry): loader composto tolerante a falhas + filtro de kwargs por assinatura  
**Data:** 2026-08-18

## Arquivos Alterados

- `db/registry.py`

## O que foi alterado

### 1. `_loader_composto()` — tolerância a falhas parciais

Antes, o loader composto executava todos os loaders em sequência sem tratamento de erro — se qualquer loader falhasse, o erro propagava e abortava o relatório inteiro.

Agora, cada loader individual é executado em try/except:
- Se falhar: registra `WARNING` e continua para o próximo
- Se **todos** falharem: levanta `RuntimeError` (comportamento esperado)
- Se **pelo menos 1** tiver sucesso: retorna normalmente (com warnings dos que falharam)

### 2. Filtro de kwargs por assinatura da função

`get_loader()` agora inspeciona a assinatura de cada função loader com `inspect.signature()` e filtra os kwargs para apenas os parâmetros que a função aceita.

Isso resolve o `TypeError: carregar_shopify() got an unexpected keyword argument 'tabela'` — `carregar_shopify(dias)` não aceita `tabela`, mas `tabela_db` estava configurado no bloco `shopify` do `clientes.json`.

### 3. `import inspect` adicionado

## Causa Raiz dos Erros

Os erros `"Loader não configurado para 'pod_kombucha'"` e `"Sync de dados falhou após 3 tentativas para source_of"` tinham duas causas:

1. **Meta Ads API HTTP 500 intermitente** → loader composto abortava completamente em vez de continuar com Google Ads e Shopify
2. **`carregar_shopify()` não aceitava `tabela`** → TypeError ao tentar executar o loader Shopify do `source_of`

## Impacto

| Cenário | Antes | Depois |
|---------|-------|--------|
| Meta API com erro 500 | ❌ Todo relatório falha | ✅ Continua com Google Ads + Shopify |
| `tabela` passado para `carregar_shopify` | ❌ TypeError | ✅ Parâmetro ignorado automaticamente |
| Todos os loaders falham | ❌ RuntimeError | ❌ RuntimeError (comportamento mantido) |
| `pod_kombucha` com só Meta Ads ativo | ❌ Sem loader válido | ✅ Tenta Meta; só falha se Meta falhar |

## Correção adicional — Crons com `source venv/bin/activate`

Os crons `shopify-sync-horario` e `sheets-sync-sqlite` usavam `source venv/bin/activate && python ...` — bloqueado pelo preflight do OpenClaw.

Corrigidos para usar `venv/bin/python ...` diretamente.
