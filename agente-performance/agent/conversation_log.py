"""
agent/conversation_log.py

Log estruturado de conversas para auditoria e melhoria do agente.
Armazena: pergunta, resposta, intent, campanha, período, user_id, latência.

Schema: conversation_logs (SQLite em data/logs.db)
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
LOG_DB   = BASE_DIR / "data" / "logs.db"

_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        LOG_DB.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(LOG_DB), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,
                user_id      TEXT,
                channel      TEXT,
                thread_ts    TEXT,
                pergunta     TEXT    NOT NULL,
                intent       TEXT,
                campanha     TEXT,
                data_inicio  TEXT,
                data_fim     TEXT,
                resposta     TEXT,
                latencia_ms  INTEGER,
                cache_hit    INTEGER DEFAULT 0,
                erro         TEXT
            )
        """)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_ts      ON conversation_logs(ts)")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_intent  ON conversation_logs(intent)")
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_user    ON conversation_logs(user_id)")
        _conn.commit()
    return _conn


def registrar(
    pergunta:    str,
    resposta:    str,
    meta:        dict,
    user_id:     str  = None,
    channel:     str  = None,
    thread_ts:   str  = None,
    latencia_ms: int  = 0,
    cache_hit:   bool = False,
    erro:        str  = None,
) -> None:
    """Registra uma conversa no log."""
    try:
        conn = _get_conn()
        conn.execute("""
            INSERT INTO conversation_logs
              (ts, user_id, channel, thread_ts, pergunta, intent, campanha,
               data_inicio, data_fim, resposta, latencia_ms, cache_hit, erro)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            time.time(),
            user_id,
            channel,
            thread_ts,
            pergunta[:1000],
            meta.get("intent"),
            meta.get("campanha"),
            meta.get("data_inicio"),
            meta.get("data_fim"),
            resposta[:3000] if resposta else None,
            latencia_ms,
            int(cache_hit),
            erro,
        ))
        conn.commit()
    except Exception as e:
        logger.warning("Falha ao registrar log: %s", e)


def resumo_uso(dias: int = 7) -> dict:
    """Retorna estatísticas de uso dos últimos N dias."""
    try:
        conn  = _get_conn()
        since = time.time() - dias * 86400
        rows  = conn.execute("""
            SELECT
                COUNT(*)                                      AS total,
                COUNT(DISTINCT user_id)                       AS usuarios_unicos,
                COUNT(DISTINCT DATE(ts, 'unixepoch'))         AS dias_ativos,
                ROUND(AVG(latencia_ms), 0)                    AS latencia_media_ms,
                SUM(cache_hit)                                AS cache_hits,
                COUNT(CASE WHEN erro IS NOT NULL THEN 1 END)  AS erros
            FROM conversation_logs
            WHERE ts >= ?
        """, (since,)).fetchone()

        intents = conn.execute("""
            SELECT intent, COUNT(*) AS n
            FROM conversation_logs
            WHERE ts >= ? AND intent IS NOT NULL
            GROUP BY intent ORDER BY n DESC LIMIT 5
        """, (since,)).fetchall()

        campanhas = conn.execute("""
            SELECT campanha, COUNT(*) AS n
            FROM conversation_logs
            WHERE ts >= ? AND campanha IS NOT NULL
            GROUP BY campanha ORDER BY n DESC LIMIT 5
        """, (since,)).fetchall()

        return {
            "periodo_dias":    dias,
            "total_consultas": rows["total"],
            "usuarios_unicos": rows["usuarios_unicos"],
            "dias_ativos":     rows["dias_ativos"],
            "latencia_media_ms": rows["latencia_media_ms"],
            "cache_hits":      rows["cache_hits"],
            "erros":           rows["erros"],
            "top_intents":     [dict(r) for r in intents],
            "top_campanhas":   [dict(r) for r in campanhas],
        }
    except Exception as e:
        logger.warning("Falha ao gerar resumo de uso: %s", e)
        return {}
