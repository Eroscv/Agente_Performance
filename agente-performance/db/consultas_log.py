"""
db/consultas_log.py

Registra todas as consultas feitas por usuários no Slack.
Tabela: consultas_usuarios

Campos salvos:
  - pergunta original
  - resposta gerada
  - intent detectada
  - cliente/plataforma identificados
  - user_id e channel Slack
  - thread_ts (para agrupar por conversa)
  - cache_hit (se veio do cache ou foi gerada)
  - duracao_ms
  - aprovado (1 quando usuário dá 👍)
"""

import json
import logging
import sqlite3
from datetime import datetime, UTC
from pathlib import Path

logger = logging.getLogger(__name__)

# DB roteado para data/logs/inbox.db via db.connections
from db.connections import get_inbox_conn as _get_db_conn

_DDL = """
CREATE TABLE IF NOT EXISTS consultas_usuarios (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pergunta        TEXT NOT NULL,
    resposta        TEXT,
    intent          TEXT,
    cliente_key     TEXT,
    plataforma      TEXT,
    data_inicio     TEXT,
    data_fim        TEXT,
    user_id         TEXT,
    channel         TEXT,
    thread_ts       TEXT,
    slack_msg_ts    TEXT,
    cache_hit       INTEGER DEFAULT 0,
    aprovado        INTEGER DEFAULT 0,
    tem_erro        INTEGER DEFAULT 0,
    duracao_ms      INTEGER,
    criado_em       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cu_user      ON consultas_usuarios(user_id);
CREATE INDEX IF NOT EXISTS idx_cu_intent    ON consultas_usuarios(intent);
CREATE INDEX IF NOT EXISTS idx_cu_cliente   ON consultas_usuarios(cliente_key);
CREATE INDEX IF NOT EXISTS idx_cu_thread    ON consultas_usuarios(thread_ts);
CREATE INDEX IF NOT EXISTS idx_cu_criado    ON consultas_usuarios(criado_em);
"""


def _conn() -> sqlite3.Connection:
    return _get_db_conn()


def _init():
    try:
        conn = _conn()
        conn.executescript(_DDL)
        conn.commit()
    except Exception as e:
        logger.warning("[ConsultasLog] Erro ao criar tabela: %s", e)


_init()


def registrar_consulta(
    pergunta: str,
    resposta: str = None,
    intent: str = None,
    cliente_key: str = None,
    plataforma: str = None,
    data_inicio: str = None,
    data_fim: str = None,
    user_id: str = None,
    channel: str = None,
    thread_ts: str = None,
    slack_msg_ts: str = None,
    cache_hit: bool = False,
    tem_erro: bool = False,
    duracao_ms: int = None,
) -> int | None:
    """
    Registra uma consulta de usuário no banco.

    Returns:
        id do registro inserido, ou None em caso de erro
    """
    try:
        conn = _conn()
        cur = conn.execute("""
            INSERT INTO consultas_usuarios (
                pergunta, resposta, intent, cliente_key, plataforma,
                data_inicio, data_fim, user_id, channel, thread_ts,
                slack_msg_ts, cache_hit, tem_erro, duracao_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pergunta[:2000] if pergunta else None,
            resposta[:4000] if resposta else None,
            intent,
            cliente_key,
            plataforma,
            data_inicio,
            data_fim,
            user_id,
            channel,
            thread_ts,
            slack_msg_ts,
            int(cache_hit),
            int(tem_erro),
            duracao_ms,
        ))
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        return row_id
    except Exception as e:
        logger.warning("[ConsultasLog] Erro ao registrar consulta: %s", e)
        return None


def aprovar_consulta(slack_msg_ts: str) -> bool:
    """Marca consulta como aprovada pelo usuário (reação 👍)."""
    try:
        conn = _conn()
        conn.execute(
            "UPDATE consultas_usuarios SET aprovado = 1 WHERE slack_msg_ts = ?",
            (slack_msg_ts,)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.warning("[ConsultasLog] Erro ao aprovar consulta: %s", e)
        return False


def listar_consultas(
    cliente_key: str = None,
    intent: str = None,
    user_id: str = None,
    apenas_aprovadas: bool = False,
    limit: int = 50,
) -> list[dict]:
    """Lista consultas com filtros opcionais."""
    try:
        conn = _conn()
        where = []
        params = []
        if cliente_key:
            where.append("cliente_key = ?")
            params.append(cliente_key)
        if intent:
            where.append("intent = ?")
            params.append(intent)
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        if apenas_aprovadas:
            where.append("aprovado = 1")

        sql = "SELECT * FROM consultas_usuarios"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY criado_em DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("[ConsultasLog] Erro ao listar consultas: %s", e)
        return []


def resumo_consultas(dias: int = 30) -> dict:
    """Retorna estatísticas de uso dos últimos N dias."""
    try:
        conn = _conn()
        r = conn.execute("""
            SELECT
                COUNT(*)                                    AS total,
                COUNT(DISTINCT user_id)                     AS usuarios_unicos,
                COUNT(DISTINCT intent)                      AS intents_usadas,
                SUM(cache_hit)                              AS cache_hits,
                SUM(aprovado)                               AS aprovadas,
                SUM(tem_erro)                               AS com_erro,
                ROUND(AVG(duracao_ms))                      AS duracao_media_ms,
                MIN(criado_em)                              AS primeira,
                MAX(criado_em)                              AS ultima
            FROM consultas_usuarios
            WHERE criado_em >= datetime('now', ?)
        """, (f"-{dias} days",)).fetchone()
        conn.close()
        return dict(r) if r else {}
    except Exception as e:
        logger.warning("[ConsultasLog] Erro no resumo: %s", e)
        return {}
