"""
db/connections.py

Gerenciador de conexões multi-DB do Agente de Performance Guará.

Arquitetura de bancos:
  data/performance/<cliente>/<fonte>.db  ← dados de performance por cliente/fonte (SQLite, escrita)
  data/logs/inbox.db                     ← recebido: consultas, contexto de thread
  data/logs/outbox.db                    ← disparado: relatórios, Slack, cron
  data/logs/agent.db                     ← estado interno: cache NLP/KB, preferências
  data/campanhas.db                      ← legado (mantido em produção até cutover)

Camada de leitura analítica:
  DuckDB (db/duck.py) substitui o SQLite :memory: federado como motor de queries analíticas.
  SQLite permanece como camada de ESCRITA (sync, logs, cache).
  reset_federated_conn() vira no-op — DuckDB lê os .db sempre frescos, sem singleton.

API pública (inalterada — drop-in):
  get_performance_conn(cliente_key, plataforma) → conexão direta ao DB de performance (SQLite)
  get_inbox_conn()                              → logs de entrada (SQLite)
  get_outbox_conn()                             → logs de saída (SQLite)
  get_agent_conn()                              → estado interno do agente (SQLite)
  get_federated_conn()                          → conexão DuckDB federada (substitui :memory: SQLite)
  query_fed(sql, params)                        → query analítica via DuckDB
  query_fed_one(sql, params)                    → query analítica via DuckDB — primeiro resultado
  reset_federated_conn()                        → no-op (DuckDB não precisa de reset)
  query_db(db_key, sql, params)                 → query em DB SQLite específico
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

# ── Mapa de DBs de performance ────────────────────────────────────────────────

PERFORMANCE_DBS: dict[tuple[str, str], Path] = {
    ("source_of",    "meta_ads"):   DATA_DIR / "performance/source_of/meta_ads.db",
    ("source_of",    "google_ads"): DATA_DIR / "performance/source_of/google_ads.db",
    ("source_of",    "shopify"):    DATA_DIR / "performance/source_of/shopify.db",
    ("pod_kombucha", "meta_ads"):   DATA_DIR / "performance/pod_kombucha/meta_ads.db",
    ("pod_kombucha", "google_ads"): DATA_DIR / "performance/pod_kombucha/google_ads.db",
    ("botica",       "meta_ads"):   DATA_DIR / "performance/botica/meta_ads.db",
}

# DBs de log
INBOX_DB  = DATA_DIR / "logs/inbox.db"
OUTBOX_DB = DATA_DIR / "logs/outbox.db"
AGENT_DB  = DATA_DIR / "logs/agent.db"

# DB legado (mantido para compatibilidade durante cutover)
LEGACY_DB = DATA_DIR / "campanhas.db"
LEGACY_DB_PATH = LEGACY_DB  # alias para compatibilidade com db/connection.py

# ── Cache de conexões (singleton por arquivo) ─────────────────────────────────

_conns: dict[str, sqlite3.Connection] = {}


def _open(path: Path, wal: bool = True) -> sqlite3.Connection:
    """Abre ou retorna conexão cacheada para um arquivo DB."""
    key = str(path.resolve())
    if key not in _conns:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _conns[key] = conn
        logger.debug("[connections] Aberto: %s", path.name)
    return _conns[key]


def close_all() -> None:
    """Fecha todas as conexões abertas. Usar apenas em shutdown."""
    for conn in _conns.values():
        try:
            conn.close()
        except Exception:
            pass
    _conns.clear()


# ── Conexões por propósito ────────────────────────────────────────────────────

def get_performance_conn(cliente_key: str, plataforma: str) -> sqlite3.Connection:
    """
    Retorna conexão direta ao DB de performance de um cliente/plataforma.

    Args:
        cliente_key: 'source_of' | 'pod_kombucha'
        plataforma:  'meta_ads' | 'google_ads' | 'shopify'

    Raises:
        KeyError: se a combinação cliente_key+plataforma não existir
    """
    key = (cliente_key, plataforma)
    path = PERFORMANCE_DBS.get(key)
    if path is None:
        available = list(PERFORMANCE_DBS.keys())
        raise KeyError(
            f"DB não mapeado: {key}. Disponíveis: {available}"
        )
    return _open(path)


def get_inbox_conn() -> sqlite3.Connection:
    """Conexão para logs de entrada (consultas, contexto de thread)."""
    return _open(INBOX_DB)


def get_outbox_conn() -> sqlite3.Connection:
    """Conexão para logs de saída (relatórios, Slack, cron)."""
    return _open(OUTBOX_DB)


def get_agent_conn() -> sqlite3.Connection:
    """Conexão para estado interno do agente (cache NLP/KB, preferências)."""
    return _open(AGENT_DB)


# ── Conexão federada com views unificadas ────────────────────────────────────

_fed_conn: Optional[sqlite3.Connection] = None

# DDL das views unificadas — criadas como TEMP VIEW sobre os DBs federados
# (SQLite exige TEMP para views cross-schema com ATTACH)
_V_PERFORMANCE_DDL = """
CREATE TEMP VIEW IF NOT EXISTS v_performance AS

SELECT
    'source_of' AS cliente_key, 'Source OF' AS cliente_nome, 'google_ads' AS plataforma,
    data, campanha, campaign_id, tipo_campanha, status,
    COALESCE(impressoes,0) AS impressoes, COALESCE(cliques,0) AS cliques,
    NULL AS alcance, COALESCE(investimento,0) AS investimento,
    COALESCE(conversoes,0) AS conversoes, COALESCE(valor_conversao,0) AS receita,
    CASE WHEN investimento>0 THEN ROUND(valor_conversao/investimento,4) ELSE NULL END AS roas,
    CASE WHEN conversoes>0  THEN ROUND(investimento/conversoes,2)      ELSE NULL END AS cpa,
    ctr, cpc_medio, NULL AS video_3s, NULL AS thruplays
FROM sof_gads.source_of_gads_diario

UNION ALL

SELECT
    'source_of', 'Source OF', 'meta_ads',
    data, campanha, campaign_id, objetivo, status_criativo,
    COALESCE(impressoes,0), COALESCE(cliques_link,0), COALESCE(alcance,0),
    COALESCE(investimento,0), COALESCE(compras,0), COALESCE(valor_compras,0),
    CASE WHEN investimento*1.1215>0 THEN ROUND(valor_compras/(investimento*1.1215),4) ELSE NULL END,
    CASE WHEN compras>0             THEN ROUND(investimento*1.1215/compras,2)          ELSE NULL END,
    CASE WHEN impressoes>0          THEN ROUND(cliques_link/impressoes*100.0,4)        ELSE NULL END,
    NULL, COALESCE(video_3s,0), COALESCE(thruplays,0)
FROM sof_meta.source_of_meta_ads_diario

UNION ALL

SELECT
    'pod_kombucha', 'POD Kombucha', 'google_ads',
    data, campanha, campaign_id, tipo_campanha, NULL,
    COALESCE(impressoes,0), COALESCE(cliques,0), NULL,
    COALESCE(investimento,0), COALESCE(conversoes,0), COALESCE(valor_conversao,0),
    CASE WHEN investimento>0 THEN ROUND(valor_conversao/investimento,4) ELSE NULL END,
    CASE WHEN conversoes>0  THEN ROUND(investimento/conversoes,2)       ELSE NULL END,
    ctr, cpc_medio, NULL, NULL
FROM pod_gads.pod_kombucha_gads_diario

UNION ALL

SELECT
    'pod_kombucha', 'POD Kombucha', 'meta_ads',
    data, campanha, campaign_id, objetivo, status_criativo,
    COALESCE(impressoes,0), COALESCE(cliques_link,0), COALESCE(alcance,0),
    COALESCE(investimento,0), COALESCE(compras,0), COALESCE(valor_compras,0),
    CASE WHEN investimento*1.1215>0 THEN ROUND(valor_compras/(investimento*1.1215),4) ELSE NULL END,
    CASE WHEN compras>0             THEN ROUND(investimento*1.1215/compras,2)          ELSE NULL END,
    CASE WHEN impressoes>0          THEN ROUND(cliques_link/impressoes*100.0,4)        ELSE NULL END,
    NULL, COALESCE(video_3s,0), COALESCE(thruplays,0)
FROM pod_meta.pod_kombucha_meta_ads_diario

UNION ALL

SELECT
    'botica', 'Botica Ouro Preto', 'meta_ads',
    data, campanha, campaign_id, objetivo, status_criativo,
    COALESCE(impressoes,0), COALESCE(cliques_link,0), COALESCE(alcance,0),
    COALESCE(investimento,0), COALESCE(compras,0), COALESCE(valor_compras,0),
    CASE WHEN investimento>0 THEN ROUND(valor_compras/investimento,4) ELSE NULL END,
    CASE WHEN compras>0      THEN ROUND(investimento/compras,2)       ELSE NULL END,
    CASE WHEN impressoes>0   THEN ROUND(cliques_link/impressoes*100.0,4) ELSE NULL END,
    NULL, COALESCE(video_3s,0), COALESCE(thruplays,0)
FROM botica_meta.botica_meta_ads_diario
"""

_CAMPANHAS_UNIFICADA_DDL = """
CREATE TEMP VIEW IF NOT EXISTS campanhas_unificada AS
SELECT
    data AS data_inicio, data AS data_fim, campanha,
    cliente_key, cliente_nome, plataforma,
    investimento, receita AS valor_compras, conversoes AS compras,
    impressoes, cliques AS cliques_link, video_3s, thruplays,
    roas AS roas_compras, cpa AS custo_por_compra, ctr AS ctr_link,
    alcance,
    NULL AS frequencia, NULL AS cpm, NULL AS compras_app, NULL AS compras_site,
    NULL AS cliques_todos, NULL AS cpc_link, NULL AS visualizacoes_landing,
    NULL AS reproducoes_video, NULL AS conversas_iniciadas,
    NULL AS adicoes_carrinho, NULL AS checkout_iniciado
FROM v_performance
"""

_SHOPIFY_DDL = """
CREATE TEMP VIEW IF NOT EXISTS shopify_receita_diaria AS
SELECT * FROM shopify_src.shopify_receita_diaria
"""


# ── Camada federada — DuckDB (Etapa 2) ──────────────────────────────────────
# SQLite :memory: + ATTACH substituído por DuckDB como motor analítico.
# API pública inalterada — drop-in para todo o codebase.

FEDERATED_DB = DATA_DIR / "federated.db"  # mantido para compatibilidade de import


def get_federated_conn():
    """
    Retorna conexão DuckDB federada (substitui SQLite :memory: + ATTACH).
    Expõe as mesmas views: v_performance, campanhas_unificada, shopify_receita_diaria.
    DuckDB lê os arquivos SQLite sempre frescos — sem singleton problemático.
    """
    from db.duck import get_duck_conn
    return get_duck_conn()


def reset_federated_conn() -> None:
    """
    No-op: DuckDB lê os .db SQLite diretamente, sempre frescos.
    Mantido para compatibilidade — chamadas existentes não precisam ser removidas.
    """
    logger.debug("[fed] reset_federated_conn: no-op (DuckDB ativo)")


# ── Helpers de query ──────────────────────────────────────────────────────────

def query_fed(sql: str, params: tuple = ()) -> list[dict]:
    """
    Query analítica — FONTE PRIMÁRIA: BigQuery.
    Fallback para DuckDB/SQLite se BQ indisponível.

    Aceita SQL escrito para campanhas_unificada/v_performance/shopify_receita_diaria
    (DuckDB/SQLite) — traduz automaticamente para BigQuery via bq_translator.
    Mesma assinatura de antes — drop-in para todo agent/queries/, relatorio/, scripts/.
    """
    try:
        from db.bq_reader import enabled as _bq_enabled, query_bq as _query_bq
        from db.bq_translator import translate as _translate
        if _bq_enabled():
            bq_sql, bq_params = _translate(sql, params)
            return _query_bq(bq_sql, bq_params if bq_params else None)
    except Exception as _bq_exc:
        logger.warning("[query_fed] BQ falhou (%s) — fallback DuckDB", _bq_exc)
    # Fallback: DuckDB local
    from db.duck import query_duck
    return query_duck(sql, params)


def query_fed_one(sql: str, params: tuple = ()) -> Optional[dict]:
    """
    Query analítica — retorna primeiro resultado ou None.
    FONTE PRIMÁRIA: BigQuery. Fallback: DuckDB/SQLite.
    """
    rows = query_fed(sql, params)
    return rows[0] if rows else None


def query_db(
    db_key: str,
    sql: str,
    params: tuple = (),
) -> list[dict]:
    """
    Query em um DB específico por chave.

    db_key: 'inbox' | 'outbox' | 'agent' |
            'source_of/meta_ads' | 'source_of/google_ads' | 'source_of/shopify' |
            'pod_kombucha/meta_ads' | 'pod_kombucha/google_ads'
    """
    if db_key == "inbox":
        conn = get_inbox_conn()
    elif db_key == "outbox":
        conn = get_outbox_conn()
    elif db_key == "agent":
        conn = get_agent_conn()
    else:
        parts = db_key.split("/")
        if len(parts) != 2:
            raise ValueError(f"db_key inválido: {db_key!r}. Use 'cliente/plataforma'.")
        conn = get_performance_conn(parts[0], parts[1])

    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


# ── Inspeção das views federadas ─────────────────────────────────────────────

def inspect_federated_views() -> dict:
    """
    Retorna metadados das views ativas na conexão federada.

    Útil para debug, healthcheck e validação pós-restart.
    Não aparece em .schema dos arquivos .db (TEMP VIEW é in-memory);
    este é o único jeito de inspecionar ao vivo.

    Retorna:
        {
            "views": ["v_performance", "campanhas_unificada", "shopify_receita_diaria"],
            "row_counts": {"v_performance": 36076, ...},
            "attached_dbs": ["sof_meta", "sof_gads", ...],
        }
    """
    conn = get_federated_conn()

    views = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_temp_master WHERE type='view' ORDER BY name"
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
        r[1] for r in conn.execute("PRAGMA database_list").fetchall()
        if r[1] not in ("main", "temp")
    ]

    return {
        "views":        views,
        "row_counts":   row_counts,
        "attached_dbs": attached,
    }
