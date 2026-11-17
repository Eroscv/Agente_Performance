"""
db/bq_translator.py

Traduz SQL escrito para DuckDB/SQLite (campanhas_unificada, v_performance,
shopify_receita_diaria) para SQL BigQuery válido.

Mapeamento de tabelas (views de consumo no gold, mesmas colunas das views locais):
  campanhas_unificada      → `guara-db.correlacao.v_agente_campanhas`
  v_performance            → `guara-db.correlacao.v_agente_performance`
  shopify_receita_diaria   → `guara-db.correlacao.v_agente_shopify_diario`

Colunas não são renomeadas: as views já têm os nomes que o agente usa.

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
CONSUMO = "correlacao"   # dataset gold, onde ficam as views de consumo do agente

# ── Mapeamento de tabelas ──────────────────────────────────────────────────────

_TABLE_MAP = {
    "campanhas_unificada":    f"`{PROJECT}.{CONSUMO}.v_agente_campanhas`",
    "v_performance":          f"`{PROJECT}.{CONSUMO}.v_agente_performance`",
    "shopify_receita_diaria": f"`{PROJECT}.{CONSUMO}.v_agente_shopify_diario`",
}

# ── Mapeamento de colunas ─────────────────────────────────────────────────────
# Vazio de propósito: as views de consumo do gold (bigquery/scripts/views/correlacao/
# v_agente_*.sql) têm as mesmas colunas das views locais. Renomear coluna por regex trocava
# também os apelidos de saída (AS conversoes, AS data_inicio...) e quebrava quem lia o resultado.

_COLUMN_MAP: dict[str, str] = {}

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


# Colunas das views de consumo (correlacao.v_agente_*). Usadas só para qualificar colunas
# dentro de agregações em HAVING/ORDER BY; manter em dia com bigquery/scripts/views/correlacao/.
_VIEW_COLUMNS = frozenset("""
    cliente_key cliente_nome plataforma data data_inicio data_fim campanha campaign_id objetivo status
    impressoes cliques cliques_link alcance investimento conversoes compras receita valor_compras
    roas roas_compras cpa custo_por_compra ctr ctr_link cpc_medio video_3s thruplays
    frequencia cpm compras_app compras_site cliques_todos cpc_link visualizacoes_landing
    reproducoes_video conversas_iniciadas adicoes_carrinho checkout_iniciado
    frete pedidos novos_clientes recompradores ticket_medio total_desconto pedidos_acima_300
    receita_criada pedidos_criados pedidos_meta receita_meta novos_meta pedidos_google receita_google
    novos_google pedidos_outros receita_outros novos_outros gross_sales pedidos_gt300 sessoes
    sessoes_com_carrinho sessoes_checkout sessoes_compra taxa_conversao is_bot_suspect
""".split())

_SQL_KEYWORDS = frozenset(
    "WHERE GROUP ORDER HAVING LIMIT UNION JOIN LEFT RIGHT INNER OUTER CROSS FULL ON USING "
    "WINDOW QUALIFY EXCEPT INTERSECT".split()
)
_VIEW_REF_RE = re.compile(r"`[\w-]+\.[\w-]+\.v_agente_\w+`")
_CLAUSE_RE = re.compile(r"\b(FROM|HAVING|ORDER\s+BY|LIMIT|UNION|QUALIFY|WINDOW)\b", re.IGNORECASE)
_AGG_RE = re.compile(r"\b(SUM|COUNT|AVG|MAX|MIN)\s*\(", re.IGNORECASE)
_ALIAS_RE = re.compile(r"\s+(?:AS\s+)?([A-Za-z_]\w*)", re.IGNORECASE)
_VIEW_ALIAS = "_v"


def _depths(sql: str) -> list[int]:
    """Profundidade de parênteses de cada caractere (ignora o que está entre aspas simples)."""
    out, depth, in_str = [], 0, False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
        elif not in_str and ch == ")":
            depth -= 1
        out.append(depth)
        if not in_str and ch == "(":
            depth += 1
    return out


def _qualify_agg_columns(sql: str) -> str:
    """
    Qualifica com o apelido da view as colunas dentro de agregações em HAVING e ORDER BY.

    No BigQuery, um apelido do SELECT vale dentro do HAVING e do ORDER BY. Em
        SELECT ROUND(SUM(investimento), 2) AS investimento ... HAVING SUM(investimento) >= 50
    o `investimento` do HAVING vira o apelido e a consulta falha com "Aggregations of
    aggregations are not allowed". No SQLite/DuckDB ele é a coluna. Com `SUM(_v.investimento)`
    o BigQuery também lê a coluna, sem reescrever a consulta.

    Só mexe em HAVING/ORDER BY cujo FROM, no mesmo nível de parênteses, é uma view de consumo.
    """
    depths = _depths(sql)
    clauses = [(m.start(), m.end(), re.sub(r"\s+", " ", m.group(1).upper())) for m in _CLAUSE_RE.finditer(sql)]
    edits: list[tuple[int, int, str]] = []      # (início, fim, texto novo)
    aliased: dict[int, str] = {}                # posição do FROM → apelido

    for idx, (start, end, kind) in enumerate(clauses):
        if kind not in ("HAVING", "ORDER BY"):
            continue
        d = depths[start]
        # FROM mais próximo antes desta cláusula, no mesmo grupo de parênteses
        from_end = None
        for p_start, p_end, p_kind in reversed(clauses[:idx]):
            if min(depths[p_start:start + 1]) < d:
                break                           # saiu do grupo de parênteses
            if p_kind == "FROM" and depths[p_start] == d:
                from_end = p_end
                break
        if from_end is None:
            continue
        view = _VIEW_REF_RE.match(sql, from_end + len(sql[from_end:]) - len(sql[from_end:].lstrip()))
        if not view:
            continue
        if from_end not in aliased:
            am = _ALIAS_RE.match(sql, view.end())
            if am and am.group(1).upper() not in _SQL_KEYWORDS:
                aliased[from_end] = am.group(1)
            else:
                aliased[from_end] = _VIEW_ALIAS
                edits.append((view.end(), view.end(), f" AS {_VIEW_ALIAS}"))
        alias = aliased[from_end]

        # Corpo da cláusula: até a próxima cláusula no mesmo nível ou o fim do grupo
        body_end = len(sql)
        for q in range(end, len(sql)):
            if depths[q] < d:
                body_end = q
                break
        for n_start, _n_end, n_kind in clauses[idx + 1:]:
            if n_start >= body_end:
                break
            if depths[n_start] == d and n_kind != "FROM":
                body_end = n_start
                break

        for am in _AGG_RE.finditer(sql, end, body_end):
            arg_start = am.end()
            arg_end = arg_start
            while arg_end < body_end and depths[arg_end] > depths[am.end() - 1]:
                arg_end += 1
            for cm in re.finditer(r"(?<![\w.`@])([A-Za-z_]\w*)\b(?!\s*\(|\.)", sql[arg_start:arg_end]):
                if cm.group(1).lower() in _VIEW_COLUMNS:
                    edits.append((arg_start + cm.start(1), arg_start + cm.end(1), f"{alias}.{cm.group(1)}"))

    for a, b, text in sorted(set(edits), reverse=True):
        sql = sql[:a] + text + sql[b:]
    return sql


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

    # 5. Colunas dentro de agregações em HAVING/ORDER BY apontam para a view, não para o
    #    apelido de mesmo nome do SELECT
    translated = _qualify_agg_columns(translated)

    # 6. Remove backtick duplos que podem surgir de substituições
    translated = re.sub(r"``", "`", translated)

    logger.debug("[BQTranslator] SQL original: %.200s", sql.strip())
    logger.debug("[BQTranslator] SQL traduzido: %.200s", translated.strip())
    logger.debug("[BQTranslator] Params BQ: %s", bq_params)

    return translated, bq_params
