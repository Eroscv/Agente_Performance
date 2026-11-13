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
    "sof_meta":        DATA_DIR / "performance/source_of/meta_ads.db",
    "sof_gads":        DATA_DIR / "performance/source_of/google_ads.db",
    "pod_meta":        DATA_DIR / "performance/pod_kombucha/meta_ads.db",
    "pod_gads":        DATA_DIR / "performance/pod_kombucha/google_ads.db",
    "shopify_src":     DATA_DIR / "performance/source_of/shopify_receita.db",
    "shopify_sess_src": DATA_DIR / "performance/source_of/shopify.db",
    "botica_meta":     DATA_DIR / "performance/botica/meta_ads.db",
    "pod_shopify":     DATA_DIR / "performance/pod_kombucha/shopify_receita.db",
    "botica_shopify":  DATA_DIR / "performance/botica/shopify_receita.db",
}

# ── DDL das views (idêntico ao connections.py para garantir paridade) ─────────

_V_PERFORMANCE_DDL = """
CREATE OR REPLACE VIEW v_performance_linhas AS

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

UNION ALL

SELECT
    'botica'           AS cliente_key,
    'Botica Ouro Preto' AS cliente_nome,
    'meta_ads'         AS plataforma,
    data, campanha, campaign_id, objetivo, status_criativo,
    COALESCE(impressoes, 0), COALESCE(cliques_link, 0), COALESCE(alcance, 0),
    COALESCE(investimento, 0), COALESCE(compras, 0), COALESCE(valor_compras, 0),
    CASE WHEN investimento > 0
         THEN ROUND(valor_compras / investimento, 4) END,
    CASE WHEN compras > 0
         THEN ROUND(investimento / compras, 2) END,
    CASE WHEN impressoes > 0
         THEN ROUND(cliques_link / impressoes * 100.0, 4) END,
    NULL,
    COALESCE(video_3s, 0), COALESCE(thruplays, 0)
FROM botica_meta.botica_meta_ads_diario
"""

# Meta grava uma linha por anúncio; quem lê v_performance espera uma linha por campanha/dia
# (somar ROAS/CTR de anúncios, contar campanhas e listar "por campanha" saía duplicado).
# Taxas recalculadas sobre as somas; Meta de Source OF e POD mantém o investimento com imposto
# (1,1215) no ROAS/CPA, como na view por linha.
_V_PERFORMANCE_AGG_DDL = """
CREATE OR REPLACE VIEW v_performance AS
SELECT
    cliente_key, ANY_VALUE(cliente_nome) AS cliente_nome, plataforma, data, campanha,
    ANY_VALUE(campaign_id) AS campaign_id, ANY_VALUE(objetivo) AS objetivo, ANY_VALUE(status) AS status,
    SUM(impressoes) AS impressoes, SUM(cliques) AS cliques,
    CASE WHEN plataforma = 'meta_ads' THEN MAX(alcance) END AS alcance,  -- alcance não soma entre anúncios
    SUM(investimento) AS investimento, SUM(conversoes) AS conversoes, SUM(receita) AS receita,
    ROUND(SUM(receita) / NULLIF(SUM(investimento) * imp, 0), 4)     AS roas,
    ROUND(SUM(investimento) * imp / NULLIF(SUM(conversoes), 0), 2)  AS cpa,
    ROUND(SUM(cliques) * 100.0 / NULLIF(SUM(impressoes), 0), 4)     AS ctr,
    ROUND(SUM(investimento) / NULLIF(SUM(cliques), 0), 2)           AS cpc_medio,
    SUM(video_3s) AS video_3s, SUM(thruplays) AS thruplays
FROM (SELECT *, CASE WHEN plataforma = 'meta_ads' AND cliente_key IN ('source_of', 'pod_kombucha')
                     THEN 1.1215 ELSE 1 END AS imp
      FROM v_performance_linhas)
GROUP BY cliente_key, plataforma, data, campanha, imp
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

# ── Paths do Data Warehouse (Parquet) ───────────────────────────────────────

WH_DIR = BASE_DIR / "data" / "warehouse"

# ── DDL views DW ─────────────────────────────────────────────────────────────

# Pedidos Shopify de todas as lojas, com cliente_key. shopify_receita_diaria (abaixo) continua só
# Source OF porque ~40 consultas leem sem filtro de cliente.
_SHOPIFY_POR_CLIENTE_DDL = """
CREATE OR REPLACE VIEW shopify_por_cliente AS
SELECT 'source_of' AS cliente_key, CAST(data AS VARCHAR) AS data, receita, pedidos, novos_clientes,
       recompradores, ticket_medio, total_desconto, pedidos_acima_300 FROM shopify_src.shopify_receita_diaria
UNION ALL
SELECT 'pod_kombucha', CAST(data AS VARCHAR), receita, pedidos, novos_clientes,
       recompradores, ticket_medio, total_desconto, pedidos_acima_300 FROM pod_shopify.shopify_receita_diaria
UNION ALL
SELECT 'botica', CAST(data AS VARCHAR), receita, pedidos, novos_clientes,
       recompradores, ticket_medio, total_desconto, pedidos_acima_300 FROM botica_shopify.shopify_receita_diaria
"""

_SHOPIFY_DDL = """
CREATE OR REPLACE VIEW shopify_receita_diaria AS
SELECT
    CAST(r.data AS VARCHAR)          AS data,
    r.receita,
    COALESCE(r.frete, 0.0)           AS frete,
    r.pedidos,
    r.novos_clientes,
    r.recompradores,
    r.ticket_medio,
    r.total_desconto,
    r.pedidos_acima_300,
    COALESCE(r.receita_criada, 0.0)  AS receita_criada,
    COALESCE(r.pedidos_criados, 0)   AS pedidos_criados,
    COALESCE(r.pedidos_meta, 0)      AS pedidos_meta,
    COALESCE(r.receita_meta, 0.0)    AS receita_meta,
    COALESCE(r.novos_meta, 0)        AS novos_meta,
    COALESCE(r.pedidos_google, 0)    AS pedidos_google,
    COALESCE(r.receita_google, 0.0)  AS receita_google,
    COALESCE(r.novos_google, 0)      AS novos_google,
    COALESCE(r.pedidos_outros, 0)    AS pedidos_outros,
    COALESCE(r.receita_outros, 0.0)  AS receita_outros,
    COALESCE(r.novos_outros, 0)      AS novos_outros,
    COALESCE(r.gross_sales, 0.0)     AS gross_sales,
    COALESCE(r.pedidos_gt300, 0)     AS pedidos_gt300,
    s.sessoes,
    s.sessoes_com_carrinho,
    s.sessoes_checkout,
    s.sessoes_compra,
    s.taxa_conversao,
    s.is_bot_suspect
FROM shopify_src.shopify_receita_diaria r
LEFT JOIN shopify_sess_src.source_of_shopify_sessoes s
    ON CAST(s.data AS VARCHAR) = CAST(r.data AS VARCHAR)
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
        conn.execute(_V_PERFORMANCE_AGG_DDL)
        conn.execute(_CAMPANHAS_UNIFICADA_DDL)
        logger.debug("[duck] Views v_performance e campanhas_unificada criadas")
    except Exception as e:
        logger.warning("[duck] Erro ao criar views de performance: %s", e)

    try:
        conn.execute(_SHOPIFY_DDL)
        conn.execute(_SHOPIFY_POR_CLIENTE_DDL)
        logger.debug("[duck] View shopify_receita_diaria criada")
    except Exception as e:
        logger.warning("[duck] Erro ao criar view Shopify: %s", e)

    logger.info("[duck] Conexão DuckDB inicializada (%d DBs attached)", len(attached))

    # ── Views DW Parquet (se data/warehouse existir) ──────────────────────────
    _criar_views_dw(conn)

    return conn


def _criar_views_dw(conn: duckdb.DuckDBPyConnection) -> None:
    """Cria views DW apontando para Parquet. Silencioso se arquivos não existem."""
    perf_glob   = str(WH_DIR / "fato_performance" / "*.parquet")
    ads_glob    = str(WH_DIR / "fato_ads"         / "*.parquet")
    shopify_glob = str(WH_DIR / "fato_shopify"    / "*.parquet")
    dim_campanha = str(WH_DIR / "dim_campanha.parquet")
    dim_cliente  = str(WH_DIR / "dim_cliente.parquet")
    dim_tempo    = str(WH_DIR / "dim_tempo.parquet")

    # Só cria a view se há pelo menos um arquivo Parquet
    has_perf    = bool(list((WH_DIR / "fato_performance").glob("*.parquet")))
    has_ads     = bool(list((WH_DIR / "fato_ads").glob("*.parquet")))
    has_shopify = bool(list((WH_DIR / "fato_shopify").glob("*.parquet")))
    has_dims    = (WH_DIR / "dim_campanha.parquet").exists()

    if not (has_perf or has_ads or has_shopify):
        logger.debug("[duck] DW Parquet não encontrado — views dw_* não criadas")
        return

    ddls = []
    if has_perf:
        ddls.append(f"CREATE OR REPLACE VIEW dw_performance AS SELECT * FROM read_parquet('{perf_glob}', union_by_name=true)")
    if has_ads:
        ddls.append(f"CREATE OR REPLACE VIEW dw_ads AS SELECT * FROM read_parquet('{ads_glob}', union_by_name=true)")
    if has_shopify:
        ddls.append(f"CREATE OR REPLACE VIEW dw_shopify AS SELECT * FROM read_parquet('{shopify_glob}', union_by_name=true)")
    if has_dims:
        ddls.append(f"CREATE OR REPLACE VIEW dw_dim_campanha AS SELECT * FROM read_parquet('{dim_campanha}')")
        ddls.append(f"CREATE OR REPLACE VIEW dw_dim_cliente  AS SELECT * FROM read_parquet('{dim_cliente}')")
        ddls.append(f"CREATE OR REPLACE VIEW dw_dim_tempo    AS SELECT * FROM read_parquet('{dim_tempo}')")

    created = []
    for ddl in ddls:
        try:
            conn.execute(ddl)
            view_name = ddl.split("VIEW ")[1].split(" AS")[0].strip()
            created.append(view_name)
        except Exception as e:
            logger.warning("[duck] Erro ao criar view DW: %s", e)

    if created:
        logger.info("[duck] Views DW criadas: %s", ", ".join(created))


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
