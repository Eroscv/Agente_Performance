"""
agent/sql_validator.py — Validador de SQL em 4 camadas.

Camada 1 — Segurança:   apenas SELECT, sem DDL/DML, subqueries seguras
Camada 2 — Schema:      colunas e tabelas existem no banco
Camada 3 — Negócio:     ROAS calculado corretamente, divisão segura, datas ISO
Camada 4 — Performance: sem full-scan, LIMIT obrigatório, cap de 50 linhas

Integração com sql_generator.py:
    from agent.sql_validator import validate, ValidationResult
    result = validate(sql, cliente_key="source_of")
    if result.has_errors():
        sql = result.fixed_sql   # SQL corrigido automaticamente quando possível
        # ou: raise ValueError(result.summary())

Cada issue tem:
    level:   "error" | "warning" | "fixed"
    code:    identificador único (ex: "NO_SELECT", "AVG_ROAS", "DIV_NO_NULLIF")
    message: descrição legível
    fix:     descrição da correção aplicada (se auto-fixável)
"""

import logging
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

import sqlglot
import sqlglot.expressions as exp

from db.connections import LEGACY_DB_PATH as DB_PATH

logger = logging.getLogger(__name__)

# ── Schema em memória (carregado uma vez) ────────────────────────────────────

# Tabelas e views permitidas para o SQL dinâmico
_ALLOWED_TABLES = {
    "campanhas_unificada",
    "ads_diario",
    "campanhas",
    "v_performance",
    "v_campanhas_sheets",
    "shopify_receita_diaria",
    "source_of_meta_ads_diario",
    "source_of_gads_diario",
    "pod_kombucha_meta_ads_diario",
    "pod_kombucha_gads_diario",
}

# Métricas que o LLM tende a calcular errado via AVG simples
_ROAS_COLS   = {"roas_compras", "roas_real", "roas"}
_AVOID_AVG   = _ROAS_COLS | {"cpa", "custo_por_compra", "cpc_link", "cpm", "ctr_link"}

# Colunas de data que precisam de formato ISO
_DATE_COLS   = {"data_inicio", "data_fim", "data"}

# Máximo de linhas permitido
_MAX_LIMIT   = 50

# Regex de detecção rápida (pré-parse)
_FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|PRAGMA|VACUUM|TRUNCATE)\b",
    re.IGNORECASE,
)
_NON_ISO_DATE_RE = re.compile(r"\b(\d{2})[/\-](\d{2})[/\-](\d{4})\b")  # DD/MM/YYYY
_LIMIT_RE        = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
_SELECT_RE       = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_CLKEY_RE        = re.compile(r"cliente_key\s*=\s*['\"]?\w+['\"]?", re.IGNORECASE)


@lru_cache(maxsize=1)
def _load_schema() -> dict[str, set[str]]:
    """
    Carrega o schema do banco em memória (colunas por tabela/view).
    Cache: chamado apenas uma vez por processo.
    """
    schema: dict[str, set[str]] = {}
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
        for t in tables:
            name = t["name"]
            cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
            schema[name] = {c["name"].lower() for c in cols}
        conn.close()
        logger.debug("[SQLValidator] Schema carregado: %d objetos", len(schema))
    except Exception as e:
        logger.warning("[SQLValidator] Falha ao carregar schema: %s", e)
    return schema


# ── Estruturas de resultado ──────────────────────────────────────────────────

@dataclass
class Issue:
    level:   str   # "error" | "warning" | "fixed"
    code:    str
    message: str
    fix:     str = ""

    def __str__(self):
        prefix = {"error": "❌", "warning": "⚠️", "fixed": "🔧"}.get(self.level, "•")
        fix_str = f" → {self.fix}" if self.fix else ""
        return f"{prefix} [{self.code}] {self.message}{fix_str}"


@dataclass
class ValidationResult:
    original_sql: str
    fixed_sql:    str
    issues:       list[Issue] = field(default_factory=list)

    def has_errors(self) -> bool:
        return any(i.level == "error" for i in self.issues)

    def has_warnings(self) -> bool:
        return any(i.level == "warning" for i in self.issues)

    def summary(self) -> str:
        if not self.issues:
            return "✅ SQL válido"
        lines = [str(i) for i in self.issues]
        return "\n".join(lines)

    def error_messages(self) -> list[str]:
        return [i.message for i in self.issues if i.level == "error"]


# ── Camadas de validação ─────────────────────────────────────────────────────

def _check_security(sql: str) -> tuple[list[Issue], str]:
    """Camada 1: segurança — apenas SELECT, sem DDL/DML."""
    issues = []
    fixed = sql

    # Deve começar com SELECT (após extrair bloco SELECT do texto)
    match = re.search(r"(SELECT\b.*)", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        issues.append(Issue("error", "NO_SELECT", "SQL não contém SELECT"))
        return issues, fixed
    fixed = match.group(1).strip().rstrip(";")

    # Bloqueia DDL/DML
    if _FORBIDDEN_RE.search(fixed):
        found = _FORBIDDEN_RE.search(fixed).group(1).upper()
        issues.append(Issue(
            "error", "FORBIDDEN_OP",
            f"Operação não permitida: {found}. Apenas SELECT é aceito.",
        ))

    return issues, fixed


def _check_limit(sql: str) -> tuple[list[Issue], str]:
    """Camada 1b: LIMIT obrigatório e máximo de 50 linhas."""
    issues = []
    fixed = sql

    m = _LIMIT_RE.search(sql)
    if not m:
        # Injeta LIMIT antes de ponto-e-vírgula ou no final
        fixed = sql.rstrip(";") + f"\nLIMIT {_MAX_LIMIT}"
        issues.append(Issue(
            "fixed", "LIMIT_MISSING",
            f"LIMIT ausente",
            f"adicionado LIMIT {_MAX_LIMIT}",
        ))
    elif int(m.group(1)) > _MAX_LIMIT:
        n = int(m.group(1))
        fixed = _LIMIT_RE.sub(f"LIMIT {_MAX_LIMIT}", sql)
        issues.append(Issue(
            "fixed", "LIMIT_EXCEEDED",
            f"LIMIT {n} excede o máximo permitido ({_MAX_LIMIT})",
            f"reduzido para LIMIT {_MAX_LIMIT}",
        ))

    return issues, fixed


def _check_cliente_key(sql: str, cliente_key: str) -> tuple[list[Issue], str]:
    """Camada 1c: WHERE cliente_key obrigatório para isolamento entre clientes."""
    issues = []
    fixed = sql

    if not _CLKEY_RE.search(sql):
        inject = f"cliente_key = '{cliente_key}'"
        if re.search(r"\bWHERE\b", sql, re.IGNORECASE):
            fixed = re.sub(
                r"(\bWHERE\b)", rf"\1 {inject} AND ",
                sql, count=1, flags=re.IGNORECASE,
            )
        else:
            fixed = re.sub(
                r"(\b(GROUP\s+BY|ORDER\s+BY|LIMIT)\b)",
                f"WHERE {inject}\n\\1",
                sql, count=1, flags=re.IGNORECASE,
            )
            if not _CLKEY_RE.search(fixed):
                fixed = sql + f"\nWHERE {inject}"
        issues.append(Issue(
            "fixed", "NO_CLIENTE_KEY",
            "WHERE cliente_key ausente — risco de mistura de dados entre clientes",
            f"injetado: WHERE cliente_key = '{cliente_key}'",
        ))

    return issues, fixed


def _check_schema(sql: str) -> list[Issue]:
    """Camada 2: colunas e tabelas existem no banco."""
    issues = []
    schema = _load_schema()

    try:
        tree = sqlglot.parse_one(sql, dialect="sqlite")
    except Exception as e:
        issues.append(Issue("warning", "PARSE_ERROR", f"Não foi possível analisar o SQL: {e}"))
        return issues

    # Verificar tabelas
    used_tables = [t.name.lower() for t in tree.find_all(exp.Table)]
    for tbl in used_tables:
        if tbl not in schema:
            if tbl in _ALLOWED_TABLES:
                issues.append(Issue(
                    "warning", "TABLE_NOT_IN_SCHEMA",
                    f"Tabela '{tbl}' não encontrada no schema atual (pode ser view ou alias)",
                ))
            else:
                issues.append(Issue(
                    "error", "UNKNOWN_TABLE",
                    f"Tabela '{tbl}' não existe. Tabelas permitidas: campanhas_unificada, ads_diario",
                ))

    # Verificar colunas contra tabelas usadas
    valid_cols: set[str] = set()
    for tbl in used_tables:
        if tbl in schema:
            valid_cols |= schema[tbl]

    if valid_cols:
        used_cols = [c.name.lower() for c in tree.find_all(exp.Column)
                     if c.name and c.name.lower() not in {"*"}]
        for col in used_cols:
            if col not in valid_cols:
                # Pode ser alias — não errar, apenas avisar
                issues.append(Issue(
                    "warning", "UNKNOWN_COLUMN",
                    f"Coluna '{col}' não encontrada no schema "
                    f"(pode ser alias ou calculada)",
                ))

    return issues


def _check_business_rules(sql: str) -> tuple[list[Issue], str]:
    """Camada 3: regras de negócio — ROAS, divisão segura, datas ISO, GROUP BY."""
    issues = []
    fixed = sql

    try:
        tree = sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:
        return issues, fixed

    # 3a. AVG em métricas que devem ser ponderadas
    for avg_node in tree.find_all(exp.Avg):
        # Extrai o nome da coluna dentro do AVG
        inner_cols = [c.name.lower() for c in avg_node.find_all(exp.Column)]
        for col in inner_cols:
            if col in _AVOID_AVG:
                issues.append(Issue(
                    "warning", "AVG_METRIC",
                    f"AVG({col}) é estatisticamente incorreto para métricas ponderadas. "
                    f"Use SUM(valor_compras)/NULLIF(SUM(investimento),0) para ROAS, "
                    f"ou SUM(investimento)/NULLIF(SUM(compras),0) para CPA.",
                ))

    # 3b. Divisão sem NULLIF no denominador (divisão por zero)
    for div_node in tree.find_all(exp.Div):
        right_sql = div_node.right.sql().upper()
        if "NULLIF" not in right_sql and "CASE" not in right_sql:
            # Só avisa se o denominador for uma agregação (SUM, COUNT, etc.)
            has_agg = any(
                isinstance(n, (exp.Sum, exp.Count, exp.Avg, exp.Max, exp.Min))
                for n in div_node.right.find_all(exp.AggFunc)
            )
            if has_agg:
                issues.append(Issue(
                    "warning", "DIV_NO_NULLIF",
                    f"Divisão '{div_node.sql()}' sem NULLIF no denominador — "
                    f"risco de divisão por zero. Use NULLIF(denominador, 0).",
                ))

    # 3c. Datas em formato não-ISO (DD/MM/YYYY ou DD-MM-YYYY)
    if _NON_ISO_DATE_RE.search(sql):
        m = _NON_ISO_DATE_RE.search(sql)
        issues.append(Issue(
            "warning", "NON_ISO_DATE",
            f"Data em formato não-ISO detectada: '{m.group(0)}'. "
            f"Use formato ISO: YYYY-MM-DD (ex: '2026-08-01').",
        ))

    # 3d. SELECT com agregação mas sem GROUP BY (quando tem múltiplas colunas não-agregadas)
    select_exprs = list(tree.find_all(exp.Select))
    if select_exprs:
        has_agg = bool(list(tree.find_all(exp.AggFunc)))
        has_group = bool(list(tree.find_all(exp.Group)))
        non_agg_cols = []
        for col in tree.find_all(exp.Column):
            # Verifica se a coluna está dentro de uma função de agregação
            in_agg = any(
                isinstance(ancestor, exp.AggFunc)
                for ancestor in col.walk()
                if isinstance(ancestor, exp.Expression)
            )
            if not in_agg:
                non_agg_cols.append(col.name)

        if has_agg and not has_group and len(set(non_agg_cols)) > 1:
            issues.append(Issue(
                "warning", "AGG_NO_GROUP_BY",
                f"Colunas não-agregadas detectadas ({', '.join(set(non_agg_cols))}) "
                f"junto com funções de agregação sem GROUP BY.",
            ))

    return issues, fixed


def _check_performance(sql: str) -> tuple[list[Issue], str]:
    """Camada 4: performance — full-scan, SELECT *."""
    issues = []
    fixed = sql

    try:
        tree = sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:
        return issues, fixed

    # 4a. SELECT *
    if list(tree.find_all(exp.Star)):
        issues.append(Issue(
            "warning", "SELECT_STAR",
            "SELECT * retorna todas as colunas — prefira listar apenas as necessárias.",
        ))

    # 4b. Query sem WHERE em tabela grande
    has_where = bool(list(tree.find_all(exp.Where)))
    tables = [t.name.lower() for t in tree.find_all(exp.Table)]
    big_tables = {"campanhas_unificada", "source_of_meta_ads_diario",
                  "pod_kombucha_meta_ads_diario", "campanhas"}
    if not has_where and any(t in big_tables for t in tables):
        issues.append(Issue(
            "warning", "NO_WHERE",
            "Query sem filtro WHERE em tabela grande — pode resultar em varredura completa.",
        ))

    return issues, fixed


# ── Ponto de entrada ─────────────────────────────────────────────────────────

def validate(
    sql: str,
    cliente_key: str = "source_of",
    fix: bool = True,
) -> ValidationResult:
    """
    Valida e opcionalmente corrige o SQL em 4 camadas.

    Args:
        sql:         SQL gerado pelo LLM
        cliente_key: cliente para isolamento (ex: 'source_of')
        fix:         se True, aplica correções automáticas (LIMIT, cliente_key)

    Returns:
        ValidationResult com issues e sql corrigido
    """
    issues: list[Issue] = []
    working_sql = sql.strip()

    # Camada 1 — Segurança
    sec_issues, working_sql = _check_security(working_sql)
    issues.extend(sec_issues)

    # Aborta se nem SELECT foi encontrado
    if any(i.code == "NO_SELECT" or i.code == "FORBIDDEN_OP" for i in issues
           if i.level == "error"):
        return ValidationResult(original_sql=sql, fixed_sql=working_sql, issues=issues)

    if fix:
        # LIMIT
        lim_issues, working_sql = _check_limit(working_sql)
        issues.extend(lim_issues)

        # cliente_key
        ck_issues, working_sql = _check_cliente_key(working_sql, cliente_key)
        issues.extend(ck_issues)

    # Camada 2 — Schema
    schema_issues = _check_schema(working_sql)
    issues.extend(schema_issues)

    # Camada 3 — Negócio
    biz_issues, working_sql = _check_business_rules(working_sql)
    issues.extend(biz_issues)

    # Camada 4 — Performance (roda no sql pós-segurança mas pré-correções de cliente_key
    # para detectar WHERE ausente no SQL original, não no injetado)
    perf_issues, working_sql = _check_performance(sql.strip())
    issues.extend(perf_issues)

    result = ValidationResult(
        original_sql=sql,
        fixed_sql=working_sql,
        issues=issues,
    )

    if issues:
        logger.info(
            "[SQLValidator] %d issue(s): %s",
            len(issues),
            ", ".join(f"{i.level}:{i.code}" for i in issues),
        )

    return result


def validate_and_raise(sql: str, cliente_key: str = "source_of") -> str:
    """
    Valida o SQL e lança ValueError se houver erros bloqueantes.

    Returns:
        SQL corrigido (pronto para execução)

    Raises:
        ValueError: se houver erros de segurança ou schema
    """
    result = validate(sql, cliente_key=cliente_key)
    if result.has_errors():
        raise ValueError(
            "SQL inválido:\n" + "\n".join(result.error_messages())
        )
    return result.fixed_sql
