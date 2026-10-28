"""
transcricao/card_db.py

SQLite para o agente de cards.

Tabelas:
  cards_criados  — log de cada card criado via Slack (histórico permanente)
  cards_cache    — cache das consultas ao Notion (/status-cards)
  sessoes_card   — log de sessões abertas/encerradas (dedup + timeout)
"""
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "db" / "cards_slack.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Cards criados manualmente via Slack ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS cards_criados (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    criado_em      TEXT    NOT NULL DEFAULT (datetime('now')),
    user_id        TEXT    NOT NULL,
    slack_user_nome TEXT,

    -- Título no formato CLIENTE: FRENTE - Entrega
    titulo         TEXT    NOT NULL,
    cliente        TEXT,
    frente         TEXT,
    responsavel    TEXT,
    revisor        TEXT,
    prazo          TEXT,
    descricao      TEXT,

    -- Resultado
    url_notion     TEXT,
    status         TEXT    NOT NULL DEFAULT 'criado'  -- criado | falha
);
CREATE INDEX IF NOT EXISTS idx_cards_user    ON cards_criados(user_id, criado_em DESC);
CREATE INDEX IF NOT EXISTS idx_cards_titulo  ON cards_criados(titulo);

-- ── Cache de consultas ao Notion ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cards_cache (
    cache_key      TEXT    PRIMARY KEY,   -- ex: "all" ou "uid:UUID"
    atualizado_em  TEXT    NOT NULL DEFAULT (datetime('now')),
    payload_json   TEXT    NOT NULL       -- JSON da lista de cards
);

-- ── Sessões de criação de card ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessoes_card (
    user_id        TEXT    PRIMARY KEY,
    iniciada_em    TEXT    NOT NULL DEFAULT (datetime('now')),
    step_atual     TEXT,
    resultado      TEXT    -- criado | cancelado | timeout | NULL (em andamento)
);
"""

_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        _conn.commit()
        logger.info("[card-db] Banco inicializado em %s", DB_PATH)
    return _conn


# ── Sessões ───────────────────────────────────────────────────────────────────

def abrir_sessao(user_id: str) -> None:
    conn().execute(
        "INSERT OR REPLACE INTO sessoes_card (user_id, iniciada_em, step_atual, resultado) "
        "VALUES (?, datetime('now'), 'cliente_card', NULL)",
        (user_id,),
    )
    conn().commit()


def atualizar_step(user_id: str, step: str) -> None:
    conn().execute(
        "UPDATE sessoes_card SET step_atual=? WHERE user_id=?",
        (step, user_id),
    )
    conn().commit()


def encerrar_sessao(user_id: str, resultado: str) -> None:
    conn().execute(
        "UPDATE sessoes_card SET resultado=? WHERE user_id=? AND resultado IS NULL",
        (resultado, user_id),
    )
    conn().commit()


def sessoes_timeout(minutos: int = 60) -> list[str]:
    """Retorna user_ids com sessão aberta há mais de N minutos."""
    rows = conn().execute(
        "SELECT user_id FROM sessoes_card WHERE resultado IS NULL "
        "AND iniciada_em < datetime('now', ?)",
        (f"-{minutos} minutes",),
    ).fetchall()
    return [r["user_id"] for r in rows]


# ── Cards criados ─────────────────────────────────────────────────────────────

def salvar_card(
    user_id: str, titulo: str, cliente: str = "", frente: str = "",
    responsavel: str = "", revisor: str = "", prazo: str = "",
    descricao: str = "", url_notion: str = "", status: str = "criado",
    slack_user_nome: str = "",
) -> int:
    cur = conn().execute(
        """INSERT INTO cards_criados
           (user_id, slack_user_nome, titulo, cliente, frente, responsavel,
            revisor, prazo, descricao, url_notion, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, slack_user_nome, titulo, cliente, frente, responsavel,
         revisor, prazo, descricao, url_notion, status),
    )
    conn().commit()
    return cur.lastrowid


def listar_cards(user_id: str | None = None, limite: int = 10) -> list[dict]:
    if user_id:
        rows = conn().execute(
            "SELECT * FROM cards_criados WHERE user_id=? ORDER BY criado_em DESC LIMIT ?",
            (user_id, limite),
        ).fetchall()
    else:
        rows = conn().execute(
            "SELECT * FROM cards_criados ORDER BY criado_em DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Cache Notion ──────────────────────────────────────────────────────────────

import json
from datetime import datetime, timezone


def get_cache(cache_key: str, ttl_minutos: int = 5) -> list[dict] | None:
    row = conn().execute(
        "SELECT payload_json, atualizado_em FROM cards_cache WHERE cache_key=?",
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    atualizado = datetime.fromisoformat(row["atualizado_em"])
    agora = datetime.utcnow()
    if (agora - atualizado).total_seconds() > ttl_minutos * 60:
        return None  # expirado
    return json.loads(row["payload_json"])


def set_cache(cache_key: str, cards: list[dict]) -> None:
    conn().execute(
        "INSERT OR REPLACE INTO cards_cache (cache_key, atualizado_em, payload_json) "
        "VALUES (?, datetime('now'), ?)",
        (cache_key, json.dumps(cards, ensure_ascii=False)),
    )
    conn().commit()


def invalidar_cache() -> None:
    conn().execute("DELETE FROM cards_cache")
    conn().commit()


def invalidar_cache_por_usuario(responsavel_id: str) -> None:
    """Invalida cache de um usuário específico (ex: quando Notion notifica mudança)."""
    conn().execute(
        "DELETE FROM cards_cache WHERE cache_key LIKE ?",
        (f"uid:{responsavel_id}%",),
    )
    # Invalida cache 'all' também
    conn().execute("DELETE FROM cards_cache WHERE cache_key LIKE 'all:%'")
    conn().commit()
