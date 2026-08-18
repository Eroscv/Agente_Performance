"""
db/connections.py

Gerenciador de conexões multi-DB do Agente de Performance Guará.

Arquitetura de bancos:
  data/performance/<cliente>/<fonte>.db  ← dados de performance por cliente/fonte
  data/logs/inbox.db                     ← recebido: consultas, contexto de thread
  data/logs/outbox.db                    ← disparado: relatórios, Slack, cron
  data/logs/agent.db                     ← estado interno: cache NLP/KB, preferências
  data/campanhas.db                      ← legado (mantido em produção até cutover)

API pública:
  get_performance_conn(cliente_key, plataforma) → conexão direta ao DB de performance
  get_inbox_conn()                              → logs de entrada
  get_outbox_conn()                             → logs de saída
  get_agent_conn()                              → estado interno do agente
  get_federated_conn()                          → DB federado com v_performance + campanhas_unificada
                                                   (substitui get_conn() de connection.py)
  query_fed(sql, params)                        → query no DB federado
  query_db(db_key, sql, params)                 → query em DB específico
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
}

# DBs de log
INBOX_DB  = DATA_DIR / "logs/inbox.db"
OUTBOX_DB = DATA_DIR / "logs/outbox.db"
AGENT_DB  = DATA_DIR / "logs/agent.db"

# DB legado (mantido para compatibilidade durante cutover)
LEGACY_DB = DATA_DIR / "campanhas.db"

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


# DB federado: usa :memory: com TEMP VIEWs (SQLite suporta cross-schema
# em TEMP namespace mesmo sem arquivo em disco)
FEDERATED_DB = DATA_DIR / "federated.db"  # mantido para eventuais extensões


def get_federated_conn() -> sqlite3.Connection:
    """
    Retorna conexão SQLite federada com todos os DBs de performance via ATTACH.

    Expõe v_performance, campanhas_unificada e shopify_receita_diaria como
    TEMP VIEWs — todo agent/queries/ funciona sem alterar uma linha de SQL.

    Aliases de ATTACH:
      sof_meta    → source_of/meta_ads.db
      sof_gads    → source_of/google_ads.db
      pod_meta    → pod_kombucha/meta_ads.db
      pod_gads    → pod_kombucha/google_ads.db
      shopify_src → source_of/shopify_receita.db
    """
    global _fed_conn
    if _fed_conn is not None:
        return _fed_conn

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Attach dos DBs de performance
    attaches = [
        ("sof_meta",    PERFORMANCE_DBS[("source_of",    "meta_ads")]),
        ("sof_gads",    PERFORMANCE_DBS[("source_of",    "google_ads")]),
        ("pod_meta",    PERFORMANCE_DBS[("pod_kombucha", "meta_ads")]),
        ("pod_gads",    PERFORMANCE_DBS[("pod_kombucha", "google_ads")]),
        ("shopify_src", DATA_DIR / "performance/source_of/shopify_receita.db"),
    ]

    missing = []
    for alias, path in attaches:
        if not path.exists():
            missing.append(str(path))
            continue
        conn.execute(f"ATTACH DATABASE '{path}' AS {alias}")
        logger.debug("[fed] ATTACH %s → %s", alias, path.name)

    if missing:
        logger.warning("[fed] DBs ausentes (views parciais): %s", missing)

    # Cria views unificadas sobre os DBs attachados
    conn.execute(_V_PERFORMANCE_DDL)
    conn.execute(_CAMPANHAS_UNIFICADA_DDL)
    conn.execute(_SHOPIFY_DDL)
    conn.commit()

    _fed_conn = conn
    logger.info("[fed] Conexão federada inicializada (%d DBs attached)", len(attaches) - len(missing))
    return _fed_conn


def reset_federated_conn() -> None:
    """Força recriação da conexão federada (usar após ATTACH de novos DBs)."""
    global _fed_conn
    if _fed_conn is not None:
        try:
            _fed_conn.close()
        except Exception:
            pass
        _fed_conn = None


# ── Helpers de query ──────────────────────────────────────────────────────────

def query_fed(sql: str, params: tuple = ()) -> list[dict]:
    """Query no DB federado (v_performance, campanhas_unificada, shopify_receita_diaria)."""
    conn = get_federated_conn()
    cur  = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def query_fed_one(sql: str, params: tuple = ()) -> Optional[dict]:
    """Query no DB federado — retorna primeiro resultado ou None."""
    results = query_fed(sql, params)
    return results[0] if results else None


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
