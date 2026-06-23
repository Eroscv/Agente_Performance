"""
agent/context_store.py

Memória de contexto por thread do Slack.
Armazena histórico de conversas em SQLite para que o agente
entenda perguntas de acompanhamento como "e essa semana?" ou "compare com a ABO".

Schema:
  thread_context (thread_ts, channel, history_json, updated_at)

O histórico é uma lista de mensagens: [{"role": "user"|"agent", "text": "...", "meta": {...}}]
Máximo de HISTORY_LIMIT mensagens por thread para controlar tamanho do contexto.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent.parent
CTX_DB_PATH = BASE_DIR / "data" / "context.db"
HISTORY_LIMIT = 10       # máximo de turnos por thread
TTL_HOURS     = 24       # threads expiram após 24h de inatividade

_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        CTX_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(CTX_DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS thread_context (
                thread_ts   TEXT PRIMARY KEY,
                channel     TEXT,
                history     TEXT NOT NULL DEFAULT '[]',
                updated_at  TEXT NOT NULL
            )
        """)
        _conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_updated
            ON thread_context(updated_at)
        """)
        _conn.commit()
    return _conn


def get_history(thread_ts: str) -> list[dict]:
    """Retorna histórico de uma thread. Lista vazia se não existir ou expirada."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT history, updated_at FROM thread_context WHERE thread_ts = ?",
        (thread_ts,)
    ).fetchone()

    if not row:
        return []

    # Verifica TTL
    updated = datetime.fromisoformat(row["updated_at"])
    if datetime.utcnow() - updated > timedelta(hours=TTL_HOURS):
        delete_history(thread_ts)
        return []

    try:
        return json.loads(row["history"])
    except (json.JSONDecodeError, TypeError):
        return []


def append_history(thread_ts: str, channel: str, role: str, text: str, meta: dict = None):
    """
    Adiciona uma mensagem ao histórico da thread.
    role: "user" | "agent"
    """
    conn    = _get_conn()
    history = get_history(thread_ts)

    history.append({
        "role": role,
        "text": text,
        "meta": meta or {},
        "ts":   datetime.utcnow().isoformat(),
    })

    # Mantém apenas os últimos HISTORY_LIMIT turnos
    if len(history) > HISTORY_LIMIT:
        history = history[-HISTORY_LIMIT:]

    conn.execute("""
        INSERT INTO thread_context (thread_ts, channel, history, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(thread_ts) DO UPDATE SET
            history    = excluded.history,
            updated_at = excluded.updated_at
    """, (thread_ts, channel, json.dumps(history, ensure_ascii=False), datetime.utcnow().isoformat()))
    conn.commit()


def delete_history(thread_ts: str):
    """Remove histórico de uma thread."""
    conn = _get_conn()
    conn.execute("DELETE FROM thread_context WHERE thread_ts = ?", (thread_ts,))
    conn.commit()


def cleanup_expired():
    """Remove threads expiradas (> TTL_HOURS). Chamar periodicamente."""
    conn     = _get_conn()
    cutoff   = (datetime.utcnow() - timedelta(hours=TTL_HOURS)).isoformat()
    deleted  = conn.execute(
        "DELETE FROM thread_context WHERE updated_at < ?", (cutoff,)
    ).rowcount
    conn.commit()
    if deleted:
        logger.info("Context store: %d threads expiradas removidas", deleted)
    return deleted


def build_context_summary(thread_ts: str) -> str:
    """
    Monta um resumo textual do histórico da thread para injetar no prompt do Claude.
    Retorna string vazia se não houver histórico.
    """
    history = get_history(thread_ts)
    if not history:
        return ""

    linhas = ["Histórico da conversa (thread atual):"]
    for msg in history[-6:]:  # últimos 6 turnos
        role  = "Usuário" if msg["role"] == "user" else "Agente"
        texto = msg["text"][:200]  # trunca para não explodir o contexto
        meta  = msg.get("meta", {})

        linha = f"  [{role}]: {texto}"

        # Adiciona contexto da query anterior se disponível
        if msg["role"] == "agent" and meta.get("campanha"):
            linha += f" (campanha: {meta['campanha']})"
        if msg["role"] == "agent" and meta.get("data_inicio"):
            linha += f" (período: {meta['data_inicio']} a {meta.get('data_fim', '?')})"

        linhas.append(linha)

    return "\n".join(linhas)
