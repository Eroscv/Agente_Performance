# Agente de Performance Guará — Correções 16/10/2026

Documentação técnica das correções implementadas em 16/10/2026 no pipeline de dados do Agente de Performance Guará (Source OF + POD Kombucha).

---

## Sumário

1. [Classificação UTM Meta](#1-classificação-utm-meta)
2. [NULL → 0 no funil de sessões Shopify](#2-null--0-no-funil-de-sessões-shopify)
3. [Flag bot_suspect — detecção robusta e não-regressão](#3-flag-bot_suspect--detecção-robusta-e-não-regressão)
4. [Filtro `spend > 0` removido do loader Meta](#4-filtro-spend--0-removido-do-loader-meta)
5. [Reprocessamento BQ POD Kombucha (61 dias)](#5-reprocessamento-bq-pod-kombucha-61-dias)
6. [Testes](#6-testes)

---

## 1. Classificação UTM Meta

### Problema

O BigQuery contava **361 pedidos Meta** na Sem38 da POD Kombucha (14–20/set/2026), enquanto o CSV do Gerenciador de Anúncios indicava **334**. Diferença: 27 pedidos.

**Causa raiz:** a função `_classificar_utm` em `shopify/sync.py` concatenava `utm_source`, `utm_medium`, `landing_site` e `referring_site` num único campo `combined` sem hierarquia. Isso fazia com que pedidos vindos do **ManyChat** — tráfego orgânico de DM — fossem classificados como Meta, porque o `referring_site` continha referência do Facebook (`l.facebook.com`).

Distribuição dos 27 pedidos extras identificados:

| Causa | Qtd |
|---|---|
| `utm_source=manychat` com `referring_site` do Facebook | 23 |
| `utm_source` vazio, `landing_site` com `utm_source=google` (mal classificado como Meta) | 3 |
| `utm_source` vazio, `landing_site` com facebook (legítimo, mantido) | 1 |

### Solução

Lógica **hierárquica** em `_classificar_utm`:

```
1. utm_source → critério primário
   - "manychat", "klaviyo", "email", "revi" → outros (bloco explícito)
   - "facebook", "instagram", "fb", "meta", "ig" → meta
   - "google", "gclid", "cpc", "pmax" → google

2. utm_medium → segundo critério (utm_source vazio ou não reconhecido)

3. landing_site / referring_site → fallback APENAS quando utm_source = ""
   (evita que ManyChat com referring do Facebook seja contado como Meta)
```

**Arquivo:** `shopify/sync.py` — função `_classificar_utm` (linha ~191)

### Resultado

| Métrica | Antes | Depois |
|---|---|---|
| Pedidos Meta Sem38 POD (BQ) | 361 | 334 ✅ |
| Diferença vs CSV | +27 | 0 ✅ |

---

## 2. NULL → 0 no funil de sessões Shopify

### Problema

As colunas `sessoes_com_carrinho`, `sessoes_checkout` e `sessoes_compra` chegavam como `NULL` no BQ em dias onde o valor era zero. Exemplo real (POD, 06/10):

```
sess=2343  cart=NULL  checkout=NULL  compra=NULL
```

**Impacto:** `NULL` e `0` têm semânticas diferentes:
- `NULL` = dado ausente (API não retornou)
- `0` = dado presente, valor real é zero

Relatórios e médias calculados sobre colunas com `NULL` produzem resultados incorretos (o BQ ignora `NULL` em `AVG`, `SUM`, etc.).

**Causa:** o código em `scripts/sync_shopify_sessions.py` usava `int(...) or None`, transformando qualquer zero em `None`:

```python
# ANTES (bugado)
carrinho = int(r.get("sessions_with_cart_additions") or 0) or None
```

### Solução

```python
# DEPOIS (correto)
carrinho = int(r.get("sessions_with_cart_additions") or 0)
```

**Arquivo:** `scripts/sync_shopify_sessions.py` — função `buscar_sessoes`

### Observação sobre POD Kombucha

A POD usa checkout fora do Shopify nativo. O `sessions_that_reached_checkout` e `sessions_that_completed_checkout` retornam valores muito baixos (1–3/dia) porque o ShopifyQL só registra o funil do checkout nativo. Os valores gravados são os valores reais da API — não é bug de carga. Para análise de conversão da POD, usar `pedidos_criados`.

---

## 3. Flag `bot_suspect` — detecção robusta e não-regressão

### Problema

O dia **08/10/2026 da Source OF** teve **8.923 sessões** (mediana histórica: ~1.500/dia) — claramente um pico de bot. A flag `is_bot_suspect` estava chegando como `0` no BQ por dois motivos:

**Motivo 1 — Janela curta:** o sync diário busca os últimos 8 dias. Ao detectar bots, a mediana era calculada só com esses 8 dias — e quando o pico estava dentro da janela, a mediana subia junto, reduzindo o contraste.

**Motivo 2 — Sem proteção de não-regressão:** uma recarga do mesmo período sobrescrevia `is_bot_suspect=1` com `0` se o algoritmo não detectasse o pico naquela execução.

### Solução

**Correção 1 — Contexto histórico:** antes de rodar a detecção, buscar 21 dias extras de contexto histórico da API, garantindo que a mediana seja calculada com dados estáveis anteriores ao período de interesse.

```python
# Busca 21 dias de contexto antes do período
ctx_start = date_start - timedelta(days=21)
ctx_rows = buscar_sessoes(store, token, ctx_start, date_end)
ctx_rows = _detectar_bots(ctx_rows, janela_dias=14, fator=3.0)

# Filtra só os dias do período solicitado para gravar
rows = [r for r in ctx_rows if r["data"] in periodo]
```

**Correção 2 — Não-regressão:** consultar o SQLite antes de gravar e preservar qualquer `is_bot_suspect=1` já existente.

```python
# Lê flags já gravadas
cur = conn.execute(f"SELECT data FROM {tabela} WHERE is_bot_suspect = 1")
already_flagged = {row[0] for row in cur.fetchall()}

# Preserva — nunca regride para 0
for r in rows:
    if r["data"] in already_flagged:
        r["is_bot_suspect"] = 1
```

**Arquivo:** `scripts/sync_shopify_sessions.py` — função `sync_cliente`

### Resultado

| Data | Sessões | `is_bot_suspect` antes | depois |
|---|---|---|---|
| 2026-10-08 SO | 8.923 | 0 ❌ | 1 ✅ |
| Outros dias SO | ~1.500–2.300 | 0 ✅ | 0 ✅ |

---

## 4. Filtro `spend > 0` removido do loader Meta

### Problema

A query de insights da Meta Ads tinha um filtro `spend > 0` que excluía linhas de anúncios sem gasto no dia. Anúncios pausados continuam recebendo **atribuições de conversão** pela janela de 7 dias de clique — e essas conversões estavam sendo descartadas silenciosamente.

**Caso identificado:** campanha `Guara_Teste_Verticais` (05–11/10):
- `Est_Kit_Produto_Foto_SemAlcool_Oferta_140727_V7`: Meta=25, BQ=24 (−1)
- `Vid_Kit_Rafa_Talking_Promocao_Oferta_160926_30s_V02`: Meta=13, BQ=11 (−2)

**Arquivo:** `meta_ads/loader.py`

---

## 5. Reprocessamento BQ POD Kombucha (61 dias)

Após a correção da classificação UTM, reprocessados os 61 dias da POD no BQ (28/jun → 16/out/2026) para aplicar a nova lógica em todo o histórico.

```
Período reprocessado: 2026-06-28 → 2026-10-16
Pedidos lidos: 4.486 pagos | 5.113 criados
Receita total: R$ 1.202.204,81
Linhas BQ: 61 (MERGE OK)
```

Validação Sem38 pós-reprocessamento:

| Data | Meta | Google | Outros |
|---|---|---|---|
| 2026-09-14 | 51 | 9 | 22 |
| 2026-09-15 | 47 | 10 | 27 |
| 2026-09-16 | 43 | 9 | 39 |
| 2026-09-17 | 49 | 6 | 30 |
| 2026-09-18 | 46 | 10 | 31 |
| 2026-09-19 | 51 | 6 | 28 |
| 2026-09-20 | 47 | 7 | 40 |
| **Total** | **334** ✅ | **57** | **217** |

---

## 6. Testes

Os testes estão em `tests/test_correcoes_16_10.py` — **45 testes, todos passando**.

```
pytest tests/test_correcoes_16_10.py -v
45 passed in 0.10s
```

### Grupo 1 — Classificação UTM (15 testes)

| Teste | Cenário |
|---|---|
| `test_manychat_e_outros` | ManyChat → outros |
| `test_manychat_com_referring_facebook_e_outros` | ManyChat + referring FB → outros (não meta) |
| `test_manychat_com_landing_facebook_e_outros` | ManyChat + landing FB → outros |
| `test_facebook_e_meta` | utm_source=facebook → meta |
| `test_instagram_e_meta` | utm_source=instagram → meta |
| `test_facebook_ads_e_meta` | utm_source=facebook ads → meta |
| `test_fb_abreviado_e_meta` | utm_source=fb → meta |
| `test_meta_literal_e_meta` | utm_source=meta → meta |
| `test_google_e_google` | utm_source=google → google |
| `test_google_ads_e_google` | utm_source=google ads → google |
| `test_klaviyo_e_outros` | klaviyo → outros |
| `test_email_e_outros` | email → outros |
| `test_revi_e_outros` | revi → outros |
| `test_sem_utm_source_com_landing_facebook_e_meta` | fallback legítimo via landing |
| `test_sem_utm_source_sem_sinal_e_outros` | sem sinal nenhum → outros |

### Grupo 2 — Funil de Sessões NULL→0 (15 testes)

| Teste | Cenário |
|---|---|
| `test_dia_sem_carrinho_e_zero_nao_null` | 0 carrinho ≠ NULL |
| `test_dia_sem_checkout_e_zero_nao_null` | 0 checkout ≠ NULL |
| `test_dia_sem_compra_e_zero_nao_null` | 0 compra ≠ NULL |
| `test_campo_ausente_na_api_e_zero` | campo ausente → 0 |
| `test_campo_none_na_api_e_zero` | campo None → 0 |
| `test_valor_real_preservado` | valores reais não zerados |
| `test_conversao_arredondada_6_casas` | taxa_conversao com 6 casas decimais |
| `test_zero_sessoes_sem_erro` | 0 sessões não quebra |
| `test_tipo_int_carrinho` | tipo int garantido |
| `test_tipo_int_checkout` | tipo int garantido |
| `test_tipo_int_compra` | tipo int garantido |
| `test_taxa_zero_quando_sem_conversao` | conversão 0 → 0.0 |
| `test_taxa_none_vira_zero` | conversão None → 0.0 |
| `test_funil_completo_sem_null` | nenhum campo do funil é None |
| `test_string_vazia_vira_zero` | string vazia → 0 |

### Grupo 3 — Bot Suspect (15 testes)

| Teste | Cenário |
|---|---|
| `test_pico_detectado` | 3× mediana → is_bot_suspect=1 |
| `test_dias_normais_nao_marcados` | dias estáveis → 0 |
| `test_primeiros_tres_dias_sem_contexto_nao_marcados` | <3 dias de contexto → sem marcação |
| `test_fator_customizavel` | fator=2.0 detecta picos menores |
| `test_pico_abaixo_do_fator_nao_marcado` | 2.5× com fator=3.0 → não marca |
| `test_apenas_um_dia_suspeito_em_multiplos_picos` | dois picos detectados independentemente |
| `test_nao_regride_flag_ja_gravada` | flag 1 preservada em recarga |
| `test_nao_regride_nao_afeta_dias_nao_flagged` | não-regressão não afeta dias normais |
| `test_campo_is_bot_suspect_presente_em_todos_os_rows` | campo presente em todos |
| `test_is_bot_suspect_e_inteiro` | tipo 0 ou 1 (não bool) |
| `test_janela_14_dias_usa_apenas_dias_anteriores` | janela não olha para frente |
| `test_pico_isolado_nao_contamina_mediana_futura` | pico não afeta dias seguintes |
| `test_sem_rows_retorna_lista_vazia` | lista vazia → [] |
| `test_rows_com_mediana_zero_nao_marca` | mediana=0 → não marca |
| `test_08_out_so_detectado_com_contexto_historico` | replica cenário real 08/10 SO |

---

## Pendências (pós 16/10)

| Item | Status |
|---|---|
| `meta_campaign_totals` Sem42 | ⏰ automático segunda 19/10 às 06h BRT |
| Decimais Google + recálculo 14–30 dias | 🔜 após validação do `spend > 0` removido |
| Snapshot físico novos clientes (tabela imutável) | 🔜 a definir se necessário além da view |

---

## Commits desta sprint

```
9bcdadd fix(sessions): 0 em vez de NULL para funil; bot_suspect com contexto histórico + preserva flag já gravada
36b993d fix(utm): reprocessamento BQ POD 61 dias — manychat/outros fora do Meta, Sem38 334 OK
```
