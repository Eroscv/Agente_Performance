"""
db/duck.py

Camada DuckDB de leitura analítica do Agente de Performance Guará.

Arquitetura:
  - SQLite permanece como camada de ESCRITA (sync, logs, cache)
  - DuckDB atua como camada de LEITURA analítica (GROUP BY, window functions, etc.)
  - DuckDB lê os arquivos .db SQLite diretamente via extensão sqlite — sem copiar dados

API pública:
  get_duck_conn()              → conexão DuckDB in-process (singleton, thread-safe)
  query_duck(sql, params)      → list[dict] — mesma assinatura de query_fed()
  query_duck_one(sql, params)  → dict | None
  reset_duck_conn()            → força recriação (raramente necessário)

Views expostas (idênticas às do SQLite federado):
  campanhas_unificada          → todas as plataformas/clientes unificadas
  v_performance                → view base com cálculos de ROAS, CPA, CTR
  shopify_receita_diaria       → dados Shopify (Source OF)

Vantagens sobre SQLite :memory: federado:
  - Sem singleton problemático (DuckDB lê os .db sempre frescos — reset_duck_conn desnecessário)
  - Window functions nativas: AVG OVER, RANK, PERCENTILE_CONT, MEDIAN
  - QUALIFY, UNNEST, LIST_AGG, STRUCT, JSON — não disponíveis em SQLite
  - Execução paralela em queries pesadas (colunar)
  - Leitura direta de SQLite sem ATTACH manual por query
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import duckdb

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

# ── Paths dos DBs SQLite (espelha PERFORMANCE_DBS de connections.py) ──────────

_SQLITE_DBS = {
    "sof_meta":    DATA_DIR / "performance/source_of/meta_ads.db",
    "sof_gads":    DATA_DIR / "performance/source_of/google_ads.db",
    "pod_meta":    DATA_DIR / "performance/pod_kombucha/meta_ads.db",
    "pod_gads":    DATA_DIR / "performance/pod_kombucha/google_ads.db",
    "shopify_src": DATA_DIR / "performance/source_of/shopify_receita.db",
}

# ── DDL das views (idêntico ao connections.py para garantir paridade) ─────────

_V_PERFORMANCE_DDL = """
CREATE OR REPLACE VIEW v_performance AS

SELECT
    'source_of'   AS cliente_key, 'Source OF'    AS cliente_nome, 'google_ads' AS plataforma,
    data, campanha, campaign_id, tipo_campanha AS objetivo, status,
    COALESCE(impressoes, 0)       AS impressoes,
    COALESCE(cliques, 0)          AS cliques,
    NULL::DOUBLE                  AS alcance,
    COALESCE(investimento, 0)     AS investimento,
    COALESCE(conversoes, 0)       AS conversoes,
    COALESCE(valor_conversao, 0)  AS receita,
    CASE WHEN investimento > 0
         THEN ROUND(valor_conversao / investimento, 4) END AS roas,
    CASE WHEN conversoes > 0
         THEN ROUND(investimento / conversoes, 2)      END AS cpa,
    ctr, cpc_medio,
    NULL::DOUBLE AS video_3s,
    NULL::DOUBLE AS thruplays
FROM sof_gads.source_of_gads_diario

UNION ALL

SELECT
    'source_of', 'Source OF', 'meta_ads',
    data, campanha, campaign_id, objetivo, status_criativo,
    COALESCE(impressoes, 0), COALESCE(cliques_link, 0), COALESCE(alcance, 0),
    COALESCE(investimento, 0), COALESCE(compras, 0), COALESCE(valor_compras, 0),
    CASE WHEN investimento * 1.1215 > 0
         THEN ROUND(valor_compras / (investimento * 1.1215), 4) END,
    CASE WHEN compras > 0
         THEN ROUND(investimento * 1.1215 / compras, 2) END,
    CASE WHEN impressoes > 0
         THEN ROUND(cliques_link / impressoes * 100.0, 4) END,
    NULL,
    COALESCE(video_3s, 0), COALESCE(thruplays, 0)
FROM sof_meta.source_of_meta_ads_diario

UNION ALL

SELECT
    'pod_kombucha', 'POD Kombucha', 'google_ads',
    data, campanha, campaign_id, tipo_campanha, NULL,
    COALESCE(impressoes, 0), COALESCE(cliques, 0), NULL,
    COALESCE(investimento, 0), COALESCE(conversoes, 0), COALESCE(valor_conversao, 0),
    CASE WHEN investimento > 0
         THEN ROUND(valor_conversao / investimento, 4) END,
    CASE WHEN conversoes > 0
         THEN ROUND(investimento / conversoes, 2) END,
    ctr, cpc_medio, NULL, NULL
FROM pod_gads.pod_kombucha_gads_diario

UNION ALL

SELECT
    'pod_kombucha', 'POD Kombucha', 'meta_ads',
    data, campanha, campaign_id, objetivo, status_criativo,
    COALESCE(impressoes, 0), COALESCE(cliques_link, 0), COALESCE(alcance, 0),
    COALESCE(investimento, 0), COALESCE(compras, 0), COALESCE(valor_compras, 0),
    CASE WHEN investimento * 1.1215 > 0
         THEN ROUND(valor_compras / (investimento * 1.1215), 4) END,
    CASE WHEN compras > 0
         THEN ROUND(investimento * 1.1215 / compras, 2) END,
    CASE WHEN impressoes > 0
         THEN ROUND(cliques_link / impressoes * 100.0, 4) END,
    NULL,
    COALESCE(video_3s, 0), COALESCE(thruplays, 0)
FROM pod_meta.pod_kombucha_meta_ads_diario
"""

_CAMPANHAS_UNIFICADA_DDL = """
CREATE OR REPLACE VIEW campanhas_unificada AS
SELECT
    data          AS data_inicio,
    data          AS data_fim,
    campanha,
    cliente_key,
    cliente_nome,
    plataforma,
    investimento,
    receita       AS valor_compras,
    conversoes    AS compras,
    impressoes,
    cliques       AS cliques_link,
    video_3s,
    thruplays,
    roas          AS roas_compras,
    cpa           AS custo_por_compra,
    ctr           AS ctr_link,
    alcance,
    NULL::DOUBLE  AS frequencia,
    NULL::DOUBLE  AS cpm,
    NULL::DOUBLE  AS compras_app,
    NULL::DOUBLE  AS compras_site,
    NULL::DOUBLE  AS cliques_todos,
    NULL::DOUBLE  AS cpc_link,
    NULL::DOUBLE  AS visualizacoes_landing,
    NULL::DOUBLE  AS reproducoes_video,
    NULL::DOUBLE  AS conversas_iniciadas,
    NULL::DOUBLE  AS adicoes_carrinho,
    NULL::DOUBLE  AS checkout_iniciado
FROM v_performance
"""

_SHOPIFY_DDL = """
CREATE OR REPLACE VIEW shopify_receita_diaria AS
SELECT
    CAST(data AS VARCHAR)          AS data,
    receita,
    pedidos,
    novos_clientes,
    recompradores,
    ticket_medio,
    total_desconto,
    pedidos_acima_300
FROM shopify_src.shopify_receita_diaria
"""

# ── Singleton DuckDB ──────────────────────────────────────────────────────────

_duck_conn: Optional[duckdb.DuckDBPyConnection] = None


def _build_duck_conn() -> duckdb.DuckDBPyConnection:
    """Constrói conexão DuckDB in-process com todos os SQLites attachados."""
    conn = duckdb.connect(":memory:")

    # Carrega extensão SQLite (built-in no DuckDB >= 0.9)
    conn.execute("INSTALL sqlite; LOAD sqlite;")

    attached = []
    missing  = []

    for alias, path in _SQLITE_DBS.items():
        if not path.exists():
            missing.append(str(path))
            logger.warning("[duck] DB ausente: %s", path)
            continue
        # DuckDB lê SQLite diretamente via ATTACH
        conn.execute(f"ATTACH '{path}' AS {alias} (TYPE sqlite, READ_ONLY TRUE)")
        attached.append(alias)
        logger.debug("[duck] ATTACH %s → %s", alias, path.name)

    if missing:
        logger.warning("[duck] DBs ausentes (views parciais): %s", missing)

    # Cria views — só as que têm os DBs necessários
    try:
        conn.execute(_V_PERFORMANCE_DDL)
        conn.execute(_CAMPANHAS_UNIFICADA_DDL)
        logger.debug("[duck] Views v_performance e campanhas_unificada criadas")
    except Exception as e:
        logger.warning("[duck] Erro ao criar views de performance: %s", e)

    try:
        conn.execute(_SHOPIFY_DDL)
        logger.debug("[duck] View shopify_receita_diaria criada")
    except Exception as e:
        logger.warning("[duck] Erro ao criar view Shopify: %s", e)

    logger.info("[duck] Conexão DuckDB inicializada (%d DBs attached)", len(attached))
    return conn


# ── Adapter: compatibilidade com código que chama .execute().fetchall() e acessa por chave ────

class _DuckResult:
    """Wrapper sobre resultado DuckDB: rows acessíveis por índice ou nome de coluna."""
    __slots__ = ("_rows", "_cols")

    def __init__(self, cols, raw_rows):
        self._cols = cols
        self._rows = [_DuckRow(cols, r) for r in raw_rows]

    @property
    def description(self):
        """Compatibilidade sqlite3: list of (col_name, None, None, None, None, None, None)"""
        return [(c, None, None, None, None, None, None) for c in self._cols]

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _DuckRow:
    """Row DuckDB com acesso por índice inteiro e por nome de coluna (como sqlite3.Row)."""
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols, vals):
        self._cols = cols
        self._vals = vals

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._vals[self._cols.index(key)]

    def keys(self):
        return list(self._cols)

    def __iter__(self):
        return iter(self._vals)

    def __repr__(self):
        return repr(dict(zip(self._cols, self._vals)))


class _DuckConnAdapter:
    """
    Wrapper sobre DuckDBPyConnection que faz .execute() retornar _DuckResult.
    Garante compatibilidade com código que usa conn.execute().fetchall()
    e acessa rows por nome de coluna (como sqlite3.Row).
    """
    def __init__(self, conn: duckdb.DuckDBPyConnection):
        self._conn = conn

    def execute(self, sql: str, params=None):
        sql, params_list = _compat_sql(sql, tuple(params) if params else ())
        if params_list:
            cur = self._conn.execute(sql, params_list)
        else:
            cur = self._conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return _DuckResult(cols, cur.fetchall())

    # Delega outros atributos ao conn real
    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_duck_conn() -> "_DuckConnAdapter":
    """Retorna conexão DuckDB singleton via adapter compatível com sqlite3.Row."""
    global _duck_conn
    if _duck_conn is not None:
        try:
            _duck_conn.execute("SELECT 1").fetchone()
            return _DuckConnAdapter(_duck_conn)
        except Exception:
            logger.warning("[duck] Conexão morta — recriando")
            _duck_conn = None

    _duck_conn = _build_duck_conn()
    return _DuckConnAdapter(_duck_conn)


def reset_duck_conn() -> None:
    """Força recriação da conexão DuckDB. Raramente necessário."""
    global _duck_conn
    if _duck_conn is not None:
        try:
            _duck_conn.close()
        except Exception:
            pass
        _duck_conn = None


# ── Helpers de query (mesma assinatura de query_fed) ─────────────────────────

_INTERVAL_RE = None


def _compat_sql(sql: str, params: tuple) -> tuple:
    """
    Adapta SQL escrito para SQLite para funcionar no DuckDB.

    Conversões:
      DATE(?, ?)  com intervalo SQLite (ex: '-30 days')
        → (?::DATE - INTERVAL '30' DAY)
    """
    import re
    global _INTERVAL_RE
    if _INTERVAL_RE is None:
        _INTERVAL_RE = re.compile(r"DATE\(\s*\?\s*,\s*\?\s*\)", re.IGNORECASE)

    params_list = list(params)
    matches = list(_INTERVAL_RE.finditer(sql))
    if not matches:
        return sql, params_list

    # Processa do fim para o início para não deslocar índices
    for match in reversed(matches):
        q_before        = sum(1 for c in sql[:match.start()] if c == "?")
        inter_param_idx = q_before + 1   # segundo ? do DATE(?,?)

        if inter_param_idx < len(params_list):
            inter_str = str(params_list[inter_param_idx])   # ex: '-30 days'
            m2 = re.match(
                r"([+-]?\d+)\s*(day|days|month|months|year|years)",
                inter_str.strip(), re.IGNORECASE,
            )
            if m2:
                n        = int(m2.group(1))
                unit     = m2.group(2).rstrip("s").upper()   # DAY, MONTH, YEAR
                op       = "+" if n >= 0 else "-"
                abs_n    = abs(n)
                repl     = f"CAST(CAST(? AS DATE) {op} INTERVAL '{abs_n}' {unit} AS VARCHAR)"
                sql      = sql[:match.start()] + repl + sql[match.end():]
                params_list.pop(inter_param_idx)

    return sql, params_list


def query_duck(sql: str, params: tuple = ()) -> list[dict]:
    """
    Executa query analítica no DuckDB e retorna lista de dicts.
    Mesma assinatura de query_fed() — substituição drop-in.
    Aplica conversões de compatibilidade SQLite → DuckDB automaticamente.
    """
    sql, params_list = _compat_sql(sql, params)
    conn = get_duck_conn()
    result = conn.execute(sql, params_list) if params_list else conn.execute(sql)
    # _DuckResult.fetchall() retorna _DuckRow — converte para dict
    return [dict(zip(r.keys(), r)) for r in result.fetchall()]


def query_duck_one(sql: str, params: tuple = ()) -> Optional[dict]:
    """Executa query e retorna primeiro resultado ou None."""
    results = query_duck(sql, params)
    return results[0] if results else None


# ── Inspeção ──────────────────────────────────────────────────────────────────

def inspect_duck() -> dict:
    """
    Retorna metadados da conexão DuckDB ativa.
    Útil para debug e healthcheck.
    """
    conn = get_duck_conn()

    views = [
        r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_type = 'VIEW' ORDER BY table_name"
        ).fetchall()
    ]

    row_counts: dict[str, int] = {}
    for v in views:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0]
            row_counts[v] = n
        except Exception:
            row_counts[v] = -1

    attached = [
        r[0] for r in conn.execute(
            "SELECT database_name FROM duckdb_databases() "
            "WHERE database_name NOT IN ('memory', 'system', 'temp')"
        ).fetchall()
    ]

    return {
        "duckdb_version": duckdb.__version__,
        "views":          views,
        "row_counts":     row_counts,
        "attached_dbs":   attached,
    }
