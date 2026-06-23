"""
db/connection.py

Gerencia a conexão SQLite com row_factory e helpers de consulta.
Exporta:
  - get_conn()       → conexão singleton (thread-safe via check_same_thread=False)
  - query(sql, params) → list[dict]
  - query_one(sql, params) → dict | None
  - DB_PATH
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"

_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        if not DB_PATH.exists():
            raise FileNotFoundError(
                f"Banco não encontrado: {DB_PATH}\n"
                "Execute primeiro: python -m db.loader"
            )
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        logger.info("Conexão SQLite aberta: %s", DB_PATH)
    return _conn


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Executa SELECT e retorna lista de dicts."""
    conn = get_conn()
    cur  = conn.execute(sql, params)
    rows = cur.fetchall()
    return [dict(r) for r in rows]


def query_one(sql: str, params: tuple = ()) -> Optional[dict]:
    """Executa SELECT e retorna primeiro resultado ou None."""
    results = query(sql, params)
    return results[0] if results else None
