# Fix: Loader Registry — parâmetros por plataforma + wrapper Shopify

**Commit:** `a75c931`  
**Mensagem:** fix: loader registry passa params corretos por plataforma + wrapper carregar_shopify  
**Data:** 2026-08-18

---

## Arquivos Alterados

- `db/registry.py`
- `db/shopify_sync.py`
- `clientes.json`

---

## O Que Foi Alterado

### `db/registry.py`
- Adicionada função `_resolve_loader_params(plat_key, plat_cfg)` que extrai kwargs do bloco de plataforma no `clientes.json`: `account_id` (Meta), `customer_id` (Google Ads), `tabela` (tabela_db), `token_env`, mais qualquer chave em `loader_params`.
- `_loader_composto` agora chama `fn(**kwargs)` ao invés de `fn()`.
- Lista de loaders alterada de `list[Callable]` para `list[tuple[Callable, dict]]` para carregar params junto ao loader.

### `db/shopify_sync.py`
- Adicionada função `carregar_shopify(dias=8)` — wrapper sem argumentos obrigatórios. Verifica se tabela está vazia (carga inicial de 90 dias), senão sincroniza os últimos N dias. Pronta para chamar como loader de registry.

### `clientes.json`
- `source_of.plataformas.shopify.loader_fn`: `"sync_shopify"` → `"carregar_shopify"`

---

## Impacto

| Antes | Depois |
|-------|--------|
| `fn()` sem args → `TypeError: carregar_gads() missing 1 required positional argument: 'customer_id'` | `fn(customer_id="4311260590", tabela="source_of_gads_diario", dias=8)` |
| `loader_fn: "sync_shopify"` → warning + skip (função inexistente) | `loader_fn: "carregar_shopify"` → funciona corretamente |
| Loader composto com 0 funções → `ValueError: Loader não configurado para 'source_of'` | Loader composto com 2–3 funções (gads + meta + shopify) |
| Relatórios diários falhando 100% desde 0h30 | Loaders prontos para executar |

---

## Causa Raiz

Dois bugs combinados:

1. **`get_loader` não passava argumentos**: os loaders `carregar_gads` e `carregar_meta` exigem `customer_id`/`account_id` obrigatórios. O registry carregava as funções mas não passava os params → exception na importação da lista → lista ficava vazia → `ValueError: Loader não configurado`.

2. **Nome errado no `clientes.json`**: `loader_fn: "sync_shopify"` não existe em `db/shopify_sync.py`. A função certa era `sincronizar` (mas exige `data_inicio`/`data_fim`), por isso foi criado o wrapper `carregar_shopify`.
