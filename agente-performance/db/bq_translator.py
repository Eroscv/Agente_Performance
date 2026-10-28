"""
db/bq_translator.py

Traduz SQL escrito para DuckDB/SQLite (campanhas_unificada, v_performance,
shopify_receita_diaria) para SQL BigQuery válido.

Mapeamento de tabelas:
  campanhas_unificada      → `guara-db.guara_performance.ads_diario`
  v_performance            → `guara-db.guara_performance.ads_diario`
  shopify_receita_diaria   → `guara-db.guara_performance.shopify_diario`

Mapeamento de colunas (campanhas_unificada → ads_diario):
  cliente_key    → cliente
  data_inicio    → data
  data_fim       → data
  valor_compras  → receita
  compras        → compras      (igual)
  cliques_link   → cliques_link (igual)
  custo_por_compra → cpa
  roas_compras   → roas
  ctr_link       → ctr  (não existe no BQ — substituído por NULL)

Mapeamento de parâmetros:
  SQLite usa ? posicionais → BQ usa @p0, @p1, ...

Mapeamento de funções SQLite → BigQuery:
  DATE('now')         → CURRENT_DATE()
  DATE(?, ?)          → DATE_ADD(@p?, INTERVAL N DAY/DAY)  (simplificado via regex)
  strftime(...)       → FORMAT_DATE(...)
  COALESCE            → COALESCE (igual)
  NULLIF              → NULLIF (igual)
"""

from __future__ import annotations

import logging
import re
import os
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT = os.environ.get("BQ_PROJECT", "guara-db")
DATASET = os.environ.get("BQ_DATASET", "guara_performance")

# ── Mapeamento de tabelas ──────────────────────────────────────────────────────

_TABLE_MAP = {
    "campanhas_unificada":    f"`{PROJECT}.{DATASET}.ads_diario`",
    "v_performance":          f"`{PROJECT}.{DATASET}.ads_diario`",
    "shopify_receita_diaria": f"`{PROJECT}.{DATASET}.shopify_diario`",
}

# ── Mapeamento de colunas (SQLite/DuckDB → BQ) ────────────────────────────────
# Aplicado apenas em contexto de ads_diario (campanhas_unificada / v_performance)

_COLUMN_MAP = {
    r"\bclient_key\b":        "cliente",
    r"\bcliente_key\b":       "cliente",
    r"\bdata_inicio\b":       "data",
    r"\bdata_fim\b":          "data",
    r"\bvalor_compras\b":     "receita",
    r"\bcusto_por_compra\b":  "cpa",
    r"\broas_compras\b":      "roas",
    r"\bctr_link\b":          "ctr",        # aproximação — BQ calcula como cliques/impressoes
    r"\bcliente_nome\b":      "cliente",    # BQ não tem nome separado, usa cliente
    r"\bstatus_criativo\b":   "status",
    r"\bvalor_conversao\b":   "receita",
    r"\bconversoes\b":        "compras",    # Google Ads: conversoes → compras no BQ
    r"\bpedidos_acima_300\b": "pedidos_gt300",  # shopify: campo renomeado no BQ
}

# ── Mapeamento de funções SQL ──────────────────────────────────────────────────

_FUNC_MAP = [
    # DATE('now') → CURRENT_DATE()
    (re.compile(r"DATE\s*\(\s*'now'\s*\)", re.IGNORECASE), "CURRENT_DATE()"),
    # DATE(col, '-N days') → DATE_SUB(col, INTERVAL N DAY)
    (re.compile(r"DATE\s*\(\s*(\w+)\s*,\s*'-(\d+)\s*days?'\s*\)", re.IGNORECASE),
     lambda m: f"DATE_SUB({m.group(1)}, INTERVAL {m.group(2)} DAY)"),
    # DATE(col, '+N days') → DATE_ADD(col, INTERVAL N DAY)
    (re.compile(r"DATE\s*\(\s*(\w+)\s*,\s*'\+?(\d+)\s*days?'\s*\)", re.IGNORECASE),
     lambda m: f"DATE_ADD({m.group(1)}, INTERVAL {m.group(2)} DAY)"),
    # DATE(@p0, @p1) onde p1 é um intervalo como '-30 days'
    # Não traduzível estaticamente — será tratado via substituição de parâmetro
    # strftime('%Y-%m', col) → FORMAT_DATE('%Y-%m', col)
    (re.compile(r"strftime\s*\(\s*'([^']+)'\s*,\s*([^)]+)\)", re.IGNORECASE),
     lambda m: f"FORMAT_DATE('{m.group(1)}', {m.group(2).strip()})"),
    # JULIANDAY / julianday — não suportado no BQ
    (re.compile(r"julianday\s*\([^)]+\)\s*-\s*julianday\s*\([^)]+\)", re.IGNORECASE),
     "DATE_DIFF(data, data, DAY)"),  # placeholder conservativo
    # INTEGER PRIMARY KEY AUTOINCREMENT → não relevante para SELECT
    # CAST(x AS INTEGER) → CAST(x AS INT64)
    (re.compile(r"CAST\s*\(([^)]+)\s+AS\s+INTEGER\)", re.IGNORECASE),
     lambda m: f"CAST({m.group(1)} AS INT64)"),
    # CAST(x AS REAL) / CAST(x AS FLOAT) → CAST(x AS FLOAT64)
    (re.compile(r"CAST\s*\(([^)]+)\s+AS\s+(?:REAL|FLOAT)\s*\)", re.IGNORECASE),
     lambda m: f"CAST({m.group(1)} AS FLOAT64)"),
    # x::FLOAT → CAST(x AS FLOAT64)
    (re.compile(r"(\w+)::FLOAT(?:64)?", re.IGNORECASE),
     lambda m: f"CAST({m.group(1)} AS FLOAT64)"),
    # x::DOUBLE → CAST(x AS FLOAT64)
    (re.compile(r"(\w+)::DOUBLE(?:\s+PRECISION)?", re.IGNORECASE),
     lambda m: f"CAST({m.group(1)} AS FLOAT64)"),
    # NULL::DOUBLE / NULL::FLOAT → CAST(NULL AS FLOAT64)
    (re.compile(r"NULL::(?:DOUBLE|FLOAT)(?:64)?", re.IGNORECASE), "CAST(NULL AS FLOAT64)"),
    # x::INT / x::INTEGER → CAST(x AS INT64)
    (re.compile(r"(\w+)::(?:INT|INTEGER)(?:64)?", re.IGNORECASE),
     lambda m: f"CAST({m.group(1)} AS INT64)"),
    # x::TEXT / x::VARCHAR → CAST(x AS STRING)
    (re.compile(r"(\w+)::(?:TEXT|VARCHAR(?:\(\d+\))?)", re.IGNORECASE),
     lambda m: f"CAST({m.group(1)} AS STRING)"),
    # CAST(x AS TEXT) / CAST(x AS VARCHAR) → CAST(x AS STRING)
    (re.compile(r"CAST\s*\(([^)]+)\s+AS\s+(?:TEXT|VARCHAR(?:\(\d+\))?)\s*\)", re.IGNORECASE),
     lambda m: f"CAST({m.group(1)} AS STRING)"),
]

# ── Tradução de parâmetros posicionais ? → @p0, @p1, ... ────────────────────

def _translate_params(sql: str, params: tuple) -> tuple[str, list]:
    """
    Substitui ? por @p0, @p1, ... e expande parâmetros do tipo '-N days'
    que aparecem em chamadas DATE(col, ?).

    Retorna (sql_traduzido, params_lista_bq).
    """
    result_sql = sql
    bq_params: list = []
    idx = 0

    # Detecta DATE(col, ?) com param '-N days' e converte para DATE_SUB
    date_interval_pattern = re.compile(
        r"DATE\s*\(\s*(@p\d+|[?\w.]+)\s*,\s*\?\s*\)",
        re.IGNORECASE,
    )

    # Primeiro: substitui ? por marcadores temporários __Q0__, __Q1__, ...
    parts = sql.split("?")
    temp_sql = ""
    param_list = list(params)

    for i, part in enumerate(parts[:-1]):
        temp_sql += part + f"__Q{i}__"
    temp_sql += parts[-1]

    # Agora resolve cada __Qi__ no contexto
    for i, val in enumerate(param_list):
        marker = f"__Q{i}__"
        # Detecta se esse marcador está dentro de DATE(col, __Qi__)
        date_ctx = re.search(
            rf"DATE\s*\(\s*([^,)]+)\s*,\s*{re.escape(marker)}\s*\)",
            temp_sql,
            re.IGNORECASE,
        )
        if date_ctx and isinstance(val, str) and re.match(r"^[+-]\d+\s*days?$", val.strip(), re.IGNORECASE):
            # Extrai N e sinal
            m = re.match(r"^([+-])(\d+)\s*days?$", val.strip(), re.IGNORECASE)
            sign = m.group(1)
            n = m.group(2)
            col_expr = date_ctx.group(1).strip()
            # Resolve col_expr se já foi substituído por @p
            if col_expr.startswith("__Q"):
                qi = int(col_expr[3:-2])
                col_expr = f"@p{qi}"  # será resolvido depois
            fn = "DATE_SUB" if sign == "-" else "DATE_ADD"
            replacement = f"{fn}({col_expr}, INTERVAL {n} DAY)"
            temp_sql = temp_sql.replace(
                date_ctx.group(0), replacement, 1
            )
            # Remove o marcador do lugar original (já foi inlined)
            temp_sql = temp_sql.replace(marker, "", 1)
        else:
            # Substitui normalmente por @pN
            temp_sql = temp_sql.replace(marker, f"@p{idx}", 1)
            bq_params.append(val)
            idx += 1

    return temp_sql, bq_params


def _replace_cast_types(sql: str) -> str:
    """
    Substitui CAST(expr AS TYPE) onde TYPE é FLOAT, REAL, INTEGER, TEXT, VARCHAR
    pelos equivalentes BigQuery (FLOAT64, INT64, STRING).

    Usa balanceamento de parênteses para lidar com expressões aninhadas como
    CAST(SUM(COALESCE(x, 0)) AS FLOAT).
    """
    _type_map = {
        "FLOAT":   "FLOAT64",
        "REAL":    "FLOAT64",
        "FLOAT64": "FLOAT64",  # já correto
        "INTEGER": "INT64",
        "INT":     "INT64",
        "INT64":   "INT64",
        "TEXT":    "STRING",
        "VARCHAR": "STRING",
        "STRING":  "STRING",
    }
    result = []
    i = 0
    n = len(sql)
    cast_re = re.compile(r"CAST\s*\(", re.IGNORECASE)

    while i < n:
        m = cast_re.search(sql, i)
        if not m:
            result.append(sql[i:])
            break

        # Adiciona tudo antes do CAST
        result.append(sql[i:m.start()])

        # Encontra o parêntese de fechamento balanceado
        paren_start = m.end() - 1  # posição do '('
        depth = 1
        j = paren_start + 1
        while j < n and depth > 0:
            if sql[j] == '(':   depth += 1
            elif sql[j] == ')': depth -= 1
            j += 1
        # j agora aponta para o caractere após ')'
        inner = sql[paren_start + 1: j - 1]  # conteúdo entre ( )

        # Verifica se termina com ' AS TYPE'
        as_re = re.compile(r"^(.*?)\s+AS\s+(\w+)\s*$", re.IGNORECASE | re.DOTALL)
        as_m = as_re.match(inner)
        if as_m:
            expr = as_m.group(1)
            dtype = as_m.group(2).upper()
            bq_type = _type_map.get(dtype, dtype)  # mantém se não reconhecido
            result.append(f"CAST({expr} AS {bq_type})")
        else:
            # Não é um CAST de tipo simples — mantém original
            result.append(sql[m.start():j])

        i = j

    return "".join(result)


def _extract_agg_calls(text: str) -> list[str]:
    """
    Extrai todas as chamadas de agregação (SUM, COUNT, AVG, MAX, MIN) no nível superior
    de uma expressão, com balanceamento de parênteses.
    Ex: 'ROUND(SUM(COALESCE(x, 0)), 2)' -> ['SUM(COALESCE(x, 0))']
    Ex: 'SUM(a) / NULLIF(SUM(b), 0)'  -> ['SUM(a)', 'SUM(b)']
    """
    agg_re = re.compile(r"\b(SUM|COUNT|AVG|MAX|MIN)\s*\(", re.IGNORECASE)
    results = []
    n = len(text)
    for m in agg_re.finditer(text):
        # Encontra o fechamento balanceado
        depth = 1
        j = m.end()  # j aponta após '('
        while j < n and depth > 0:
            if text[j] == '(':   depth += 1
            elif text[j] == ')': depth -= 1
            j += 1
        full_agg = text[m.start(): j]  # inclui '('
        results.append(full_agg)
    return results


def _fix_having_agg(sql: str) -> str:
    """
    No BigQuery, HAVING SUM(coluna) falha com "Aggregations of aggregations"
    quando a query ja tem outras agregacoes no SELECT.

    Estrategia:
    1. Extrai mapa completo de agg -> alias do SELECT (inclui agg compostas)
    2. Substitui todas as agg no HAVING pelos aliases correspondentes
    3. Condicoes HAVING que resolveram totalmente ficam no HAVING inner (sem agg)
    4. Condicoes residuais (com agg nao mapeada) sao movidas para WHERE na outer query
    5. ORDER BY/LIMIT vao para outer com agg substituidas
    """
    import re

    having_re = re.compile(
        r"(\bHAVING\b)(.+?)(?=\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|$)",
        re.IGNORECASE | re.DOTALL,
    )
    m = having_re.search(sql)
    if not m:
        return sql

    having_body = m.group(2)
    if not re.search(r"\b(?:SUM|COUNT|AVG|MAX|MIN)\s*\(", having_body, re.IGNORECASE):
        return sql

    # Extrai mapa completo: qualquer agg call -> alias do SELECT
    # Prioridade: colunas com exatamente 1 agg (ex: SUM(investimento) -> investimento)
    # depois colunas compostas (ex: SUM(investimento) dentro de roas -> roas, se nao mapeado)
    select_re = re.compile(r"\bSELECT\b(.+?)\bFROM\b", re.IGNORECASE | re.DOTALL)
    sm = select_re.search(sql)
    agg_to_alias: dict[str, str] = {}
    if sm:
        cols = _split_top_level(sm.group(1))
        # Primeira passagem: colunas com exatamente 1 agg (prioridade)
        for col in cols:
            col = col.strip()
            as_m = re.search(r"\bAS\s+(\w+)\s*$", col, re.IGNORECASE)
            if not as_m:
                continue
            alias = as_m.group(1)
            expr = col[: as_m.start()].strip()
            agg_calls = _extract_agg_calls(expr)
            if len(agg_calls) == 1:  # so mapeia colunas simples primeiro
                agg = agg_calls[0]
                if agg not in agg_to_alias:
                    agg_to_alias[agg] = alias
        # Segunda passagem: colunas compostas (so mapeia agg ainda nao mapeadas)
        for col in cols:
            col = col.strip()
            as_m = re.search(r"\bAS\s+(\w+)\s*$", col, re.IGNORECASE)
            if not as_m:
                continue
            alias = as_m.group(1)
            expr = col[: as_m.start()].strip()
            agg_calls = _extract_agg_calls(expr)
            if len(agg_calls) > 1:
                for agg in agg_calls:
                    if agg not in agg_to_alias:
                        agg_to_alias[agg] = alias
        # Terceira passagem: mapeia variacoes de agg pela coluna base
        # Ex: SUM(COALESCE(receita, 0)) e SUM(receita) -> ambos mapeam para 'receita'
        # Garante que aliases simples (nome = coluna base) tem prioridade sobre aliases compostos
        extra_mappings: dict[str, str] = {}
        for agg_expr, alias in list(agg_to_alias.items()):
            inner_col_m = re.search(r"\b(?:SUM|COUNT|AVG|MAX|MIN)\s*\(\s*(?:COALESCE\s*\(\s*)?(\w+)",
                                    agg_expr, re.IGNORECASE)
            if not inner_col_m:
                continue
            base_col = inner_col_m.group(1).lower()
            # Alias simples: nome == coluna base (ex: investimento -> investimento)
            if alias.lower() != base_col:
                continue
            fn = re.match(r"\b(SUM|COUNT|AVG|MAX|MIN)", agg_expr, re.IGNORECASE).group(1).upper()
            # Adiciona e SOBRESCREVE variacoes que podem ter sido mapeadas para alias errado
            for variant in [f"{fn}({base_col})",
                            f"{fn}(COALESCE({base_col}, 0))",
                            f"{fn}(COALESCE({base_col},0))"]:
                # Sobrescreve se o alias atual nao corresponde a coluna base
                current_alias = agg_to_alias.get(variant, "")
                if current_alias.lower() != base_col:
                    extra_mappings[variant] = alias
        agg_to_alias.update(extra_mappings)

    # Substitui todas as agg no HAVING pelos aliases
    new_having = having_body
    for agg_expr, alias in sorted(agg_to_alias.items(), key=lambda x: -len(x[0])):
        new_having = re.sub(re.escape(agg_expr), alias, new_having, flags=re.IGNORECASE)

    # Verifica se ainda sobrou agg
    still_has_agg = bool(re.search(r"\b(?:SUM|COUNT|AVG|MAX|MIN)\s*\(", new_having, re.IGNORECASE))

    if not still_has_agg:
        # Tudo resolvido -- aplica HAVING e tambem substitui agg no ORDER BY
        sql_fixed = sql[: m.start(2)] + new_having + sql[m.end(2):]
        order_re = re.compile(r"\bORDER\s+BY\b(.+?)(?=\bLIMIT\b|$)", re.IGNORECASE | re.DOTALL)
        om = order_re.search(sql_fixed)
        if om:
            new_order = om.group(1)
            for agg_expr, alias in sorted(agg_to_alias.items(), key=lambda x: -len(x[0])):
                new_order = re.sub(re.escape(agg_expr), alias, new_order, flags=re.IGNORECASE)
            sql_fixed = sql_fixed[: om.start(1)] + new_order + sql_fixed[om.end(1):]
        return sql_fixed

    # Sobrou agg nao mapeada -- precisa de subquery
    # Divide condicoes do HAVING em: resolvidas (sem agg) e residuais (com agg)
    having_parts = _split_top_level_by_and(new_having)
    inner_having_parts = []
    outer_where_parts = []

    for part in having_parts:
        part_stripped = part.strip()
        if re.search(r"\b(?:SUM|COUNT|AVG|MAX|MIN)\s*\(", part_stripped, re.IGNORECASE):
            # Ainda tem agg -- substitui pelo alias e vai para outer WHERE
            resolved_part = part_stripped
            for agg_expr, alias in sorted(agg_to_alias.items(), key=lambda x: -len(x[0])):
                resolved_part = re.sub(re.escape(agg_expr), alias, resolved_part, flags=re.IGNORECASE)
            outer_where_parts.append(resolved_part)
        else:
            inner_having_parts.append(part_stripped)

    # Reconstroi o SQL inner sem ORDER BY/LIMIT
    inner_sql = sql
    outer_parts = []
    for clause in (r"ORDER\s+BY", r"LIMIT"):
        clause_re = re.compile(rf"\b{clause}\b(.+?)$", re.IGNORECASE | re.DOTALL)
        cm = clause_re.search(inner_sql)
        if cm:
            outer_clause = cm.group(0)
            for agg_e, alias_e in sorted(agg_to_alias.items(), key=lambda x: -len(x[0])):
                outer_clause = re.sub(re.escape(agg_e), alias_e, outer_clause, flags=re.IGNORECASE)
            outer_parts.insert(0, outer_clause)
            inner_sql = inner_sql[: cm.start()].rstrip()

    # Atualiza o HAVING inner: so condicoes sem agg residual
    m2 = having_re.search(inner_sql)
    if m2:
        if inner_having_parts:
            new_inner_having = " AND ".join(inner_having_parts)
            inner_sql = inner_sql[: m2.start(2)] + " " + new_inner_having + " " + inner_sql[m2.end(2):]
        else:
            # Remove HAVING completamente
            inner_sql = inner_sql[: m2.start()] + " " + inner_sql[m2.end():]

    # Monta outer query
    wrapped = "SELECT * FROM (\n" + inner_sql + "\n)"
    if outer_where_parts:
        wrapped += " WHERE " + " AND ".join(outer_where_parts)
    if outer_parts:
        wrapped += " " + " ".join(p.strip() for p in outer_parts)
    return wrapped


def _split_top_level_by_and(text: str) -> list[str]:
    """Divide string por AND de nivel superior (nao dentro de parenteses)."""
    parts = []
    depth = 0
    current = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '(':
            depth += 1
            current.append(text[i])
        elif text[i] == ')':
            depth -= 1
            current.append(text[i])
        elif depth == 0 and text[i:i+4].upper() == ' AND':
            after = text[i+4] if i+4 < n else ' '
            if not (after.isalnum() or after == '_'):
                parts.append(''.join(current))
                current = []
                i += 4
                continue
            else:
                current.append(text[i])
        else:
            current.append(text[i])
        i += 1
    if current:
        parts.append(''.join(current))
    return [p.strip() for p in parts if p.strip()]


def _split_top_level(text: str) -> list[str]:
    """Divide string por vírgula no nível superior (não dentro de parênteses)."""
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current))
    return parts


def translate(sql: str, params: tuple = ()) -> tuple[str, list]:
    """
    Traduz SQL SQLite/DuckDB para BigQuery SQL.

    Args:
        sql:    SQL original (para DuckDB/SQLite)
        params: parâmetros posicionais (?)

    Returns:
        (sql_bq, params_bq) prontos para query_bq()
    """
    translated = sql

    # 0. Substitui CAST(expr AS TYPE) com balanceamento de parênteses
    translated = _replace_cast_types(translated)

    # 1. Substitui nomes de tabelas
    for old_table, new_table in _TABLE_MAP.items():
        translated = re.sub(
            rf"\b{old_table}\b",
            new_table,
            translated,
            flags=re.IGNORECASE,
        )

    # 2. Substitui nomes de colunas
    for pattern, replacement in _COLUMN_MAP.items():
        translated = re.sub(pattern, replacement, translated, flags=re.IGNORECASE)

    # 3. Substitui funções SQL
    for pattern, replacement in _FUNC_MAP:
        if callable(replacement):
            translated = pattern.sub(replacement, translated)
        else:
            translated = pattern.sub(replacement, translated)

    # 4. Traduz parâmetros ? → @p0, @p1, ...
    translated, bq_params = _translate_params(translated, params)

    # 5. Fix HAVING com agregações (BQ não aceita HAVING SUM() quando há múltiplas agg)
    translated = _fix_having_agg(translated)

    # 6. Remove backtick duplos que podem surgir de substituições
    translated = re.sub(r"``", "`", translated)

    logger.debug("[BQTranslator] SQL original: %.200s", sql.strip())
    logger.debug("[BQTranslator] SQL traduzido: %.200s", translated.strip())
    logger.debug("[BQTranslator] Params BQ: %s", bq_params)

    return translated, bq_params
