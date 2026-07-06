"""
agent/cache.py

Cache com dois níveis: memória (L1) + SQLite (L2).

L1 — dict em memória: acesso instantâneo, volátil (morre no restart).
L2 — SQLite data/cache.db: persiste entre restarts, TTL respeitado.

Fluxo de leitura:  L1 → L2 → None
Fluxo de escrita:  L1 + L2 simultâneo

TTL padrão: 5 minutos (CACHE_TTL_SECONDS no .env)
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TTL      = int(os.getenv("CACHE_TTL_SECONDS", "300"))  # 5 min padrão
BASE_DIR = Path(__file__).parent.parent
CACHE_DB = BASE_DIR / "data" / "cache.db"

# ── L1: memória ───────────────────────────────────────────────────────────────
_store: dict[str, dict] = {}

# ── L2: SQLite ────────────────────────────────────────────────────────────────
_db_conn: Optional[sqlite3.Connection] = None


def _get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        _db_conn = sqlite3.connect(str(CACHE_DB), check_same_thread=False)
        _db_conn.row_factory = sqlite3.Row
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                chave      TEXT PRIMARY KEY,
                resultado  TEXT NOT NULL,
                expires_at REAL NOT NULL,
                pergunta   TEXT,
                hits       INTEGER NOT NULL DEFAULT 0
            )
        """)
        _db_conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_expires ON cache(expires_at)"
        )
        _db_conn.commit()
        # Limpa entradas expiradas ao iniciar
        _db_conn.execute("DELETE FROM cache WHERE expires_at < ?", (time.time(),))
        _db_conn.commit()
    return _db_conn


def _chave(pergunta: str) -> str:
    return hashlib.md5(pergunta.strip().lower().encode()).hexdigest()


# ── Leitura ───────────────────────────────────────────────────────────────────

def get(pergunta: str) -> Optional[dict]:
    """Retorna resultado cacheado (L1 → L2) ou None."""
    chave = _chave(pergunta)
    agora = time.time()

    # L1 — memória
    entry = _store.get(chave)
    if entry:
        if agora <= entry["expires_at"]:
            entry["hits"] += 1
            logger.debug("Cache L1 HIT (%dx): %s", entry["hits"], pergunta[:50])
            return entry["data"]
        del _store[chave]

    # L2 — SQLite
    try:
        db  = _get_db()
        row = db.execute(
            "SELECT resultado, expires_at FROM cache WHERE chave = ?", (chave,)
        ).fetchone()
        if row and agora <= row["expires_at"]:
            resultado = json.loads(row["resultado"])
            # Promove para L1
            _store[chave] = {
                "data": resultado, "expires_at": row["expires_at"], "hits": 1,
                "pergunta": pergunta[:100],
            }
            db.execute(
                "UPDATE cache SET hits = hits + 1 WHERE chave = ?", (chave,)
            )
            db.commit()
            logger.debug("Cache L2 HIT: %s", pergunta[:50])
            return resultado
    except Exception as e:
        logger.debug("Cache L2 read error: %s", e)

    return None


# ── Escrita ───────────────────────────────────────────────────────────────────

def set(pergunta: str, resultado: dict) -> None:
    """Armazena em L1 e L2."""
    chave   = _chave(pergunta)
    expires = time.time() + TTL

    # L1
    _store[chave] = {
        "data": resultado, "expires_at": expires, "hits": 0,
        "pergunta": pergunta[:100],
    }

    # L2
    try:
        db = _get_db()
        db.execute("""
            INSERT INTO cache (chave, resultado, expires_at, pergunta)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chave) DO UPDATE SET
                resultado  = excluded.resultado,
                expires_at = excluded.expires_at,
                hits       = 0
        """, (chave, json.dumps(resultado, ensure_ascii=False), expires, pergunta[:100]))
        db.commit()
    except Exception as e:
        logger.debug("Cache L2 write error: %s", e)

    logger.debug("Cache SET: %s", pergunta[:50])


# ── Manutenção ────────────────────────────────────────────────────────────────

def clear():
    """Alias direto para invalidar_tudo() — usado pelo reload do banco na API."""
    invalidar_tudo()


def invalidar_tudo() -> int:
    """Limpa L1 e L2. Usado após recarregar o banco de campanhas."""
    n = len(_store)
    _store.clear()
    try:
        db = _get_db()
        db.execute("DELETE FROM cache")
        db.commit()
        logger.info("Cache L1+L2 limpo: %d entradas removidas", n)
    except Exception as e:
        logger.debug("Cache L2 clear error: %s", e)
    return n


def stats() -> dict:
    """Estatísticas dos dois níveis."""
    agora = time.time()
    l1_ativos    = sum(1 for e in _store.values() if e["expires_at"] > agora)
    l1_expirados = len(_store) - l1_ativos
    l1_hits      = sum(e["hits"] for e in _store.values())

    l2_total = 0
    l2_hits  = 0
    try:
        db = _get_db()
        row = db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(hits),0) AS h FROM cache WHERE expires_at > ?",
            (agora,)
        ).fetchone()
        if row:
            l2_total = row["n"]
            l2_hits  = row["h"]
    except Exception:
        pass

    return {
        "l1_ativas":   l1_ativos,
        "l1_expiradas": l1_expirados,
        "l1_hits":     l1_hits,
        "l2_ativas":   l2_total,
        "l2_hits":     l2_hits,
        "ttl_segundos": TTL,
    }
