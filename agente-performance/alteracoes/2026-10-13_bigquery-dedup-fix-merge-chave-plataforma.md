# Fix: Duplicação BigQuery ads_diario — 13/10/2026

## Commit
`22fc376` — fix: MERGE chave correta por plataforma + IS NOT DISTINCT FROM para NULLs

## Arquivos alterados
- `db/bq_writer.py`

## O que foi alterado

### Problema raiz
O MERGE upsert estava usando a mesma chave para Meta e Google Ads:
`(cliente, plataforma, data, campaign_id, adset_id, ad_id)`

O Google Ads não tem `ad_id` nem `adset_id` — trabalha no nível de campanha. Isso fazia os campos chegarem como `NULL` no BQ. No SQL, `NULL = NULL` é `FALSE`, então o MERGE nunca encontrava match e **sempre inseria** uma nova linha. Com sync a cada 15min = 96 syncs/dia = até 12x duplicação.

### Fix aplicado

**1. Chave dinâmica por plataforma:**
```python
# Antes — chave única para todos:
merge_keys = {"ads_diario": ["cliente", "plataforma", "data", "campaign_id", "adset_id", "ad_id"]}

# Depois — por plataforma:
if plataforma == "google":
    keys = ["cliente", "plataforma", "data", "campaign_id"]
else:  # meta
    keys = ["cliente", "plataforma", "data", "campaign_id", "adset_id", "ad_id"]
```

**2. NULL-safe comparison no ON clause:**
```sql
-- Antes:
ON T.k = S.k  -- NULL = NULL → FALSE → sempre insere

-- Depois:
ON (T.k IS NOT DISTINCT FROM S.k)  -- NULL IS NOT DISTINCT FROM NULL → TRUE
```

## Impacto

| Data | Antes (linhas) | Depois (linhas) | Ratio antes | Ratio depois |
|---|---|---|---|---|
| 2026-10-12 | 646 | 176 | 3.7x ⚠️ | 1.0x ✅ |
| 2026-10-11 | 1.601 | 181 | 8.8x ⚠️ | 1.0x ✅ |
| 2026-10-10 | 2.084 | 184 | 11.3x ⚠️ | 1.0x ✅ |
| 2026-10-09 | 2.161 | 261 | 8.3x ⚠️ | 1.0x ✅ |
| 2026-10-08 | 1.561 | 201 | 7.8x ⚠️ | 1.0x ✅ |
| 2026-10-07 | 619 | 209 | 3.0x ⚠️ | 1.0x ✅ |
| 06/10 e antes | OK | OK | 1.0x ✅ | 1.0x ✅ |

Os 14 dias anteriores foram limpos via DELETE mantendo apenas `MAX(criado_em)` por chave.

*Corrigido por Cesar (Agente de Performance Guará) em 13/10/2026.*
