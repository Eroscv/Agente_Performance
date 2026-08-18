"""
db/slack_log.py

Registra TODOS os disparos para o Slack em `slack_disparos_log`.
Interceptado direto no _post_api de slack/poster.py.

Tabela:
  id            — autoincremento
  ts_disparo    — datetime UTC do disparo (gerado_em)
  endpoint      — ex: 'chat.postMessage', 'chat.postEphemeral'
  channel       — canal destino (ex: 'performance')
  tipo          — 'ancora', 'thread', 'alerta', 'relatorio', 'fallback', 'erro', 'outro'
  cliente_key   — 'source_of', 'pod_kombucha', '' se desconhecido
  texto_preview — primeiros 200 chars do payload text/blocks
  thread_ts     — ts da âncora (se for thread reply)
  slack_ts      — ts retornado pelo Slack (message timestamp)
  ok            — 1=sucesso, 0=falha
  erro_msg      — mensagem de erro se ok=0
  payload_size  — tamanho em bytes do payload enviado

Deduplicação:
  Antes de disparar qualquer mensagem, o chamador pode checar
  disparo_ja_enviado_hoje(tipo, cliente_key, data_ref) → bool
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"

_CREATE = """
CREATE TABLE IF NOT EXISTS slack_disparos_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_disparo    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    endpoint      TEXT    NOT NULL DEFAULT 'chat.postMessage',
    channel       TEXT,
    tipo          TEXT    NOT NULL DEFAULT 'outro',
    cliente_key   TEXT    NOT NULL DEFAULT '',
    texto_preview TEXT,
    thread_ts     TEXT,
    slack_ts      TEXT,
    ok            INTEGER NOT NULL DEFAULT 0,
    erro_msg      TEXT,
    payload_size  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_slack_log_ts       ON slack_disparos_log(ts_disparo);
CREATE INDEX IF NOT EXISTS idx_slack_log_tipo     ON slack_disparos_log(tipo, cliente_key);
CREATE INDEX IF NOT EXISTS idx_slack_log_channel  ON slack_disparos_log(channel);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_CREATE)
    return conn


def registrar_disparo(
    endpoint: str,
    channel: str,
    tipo: str,
    cliente_key: str = "",
    texto_preview: str = None,
    thread_ts: str = None,
    slack_ts: str = None,
    ok: bool = False,
    erro_msg: str = None,
    payload_size: int = None,
) -> int:
    """
    Registra um disparo Slack no banco.
    Chamado automaticamente pelo _post_api de slack/poster.py.

    Returns:
        ID do registro inserido.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = _conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO slack_disparos_log
                (ts_disparo, endpoint, channel, tipo, cliente_key,
                 texto_preview, thread_ts, slack_ts, ok, erro_msg, payload_size)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts, endpoint, channel or "", tipo, cliente_key or "",
                (texto_preview or "")[:200],
                thread_ts, slack_ts,
                int(bool(ok)), erro_msg, payload_size,
            ),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        logger.warning("[SlackLog] Erro ao registrar disparo: %s", e)
        return -1
    finally:
        conn.close()


def disparo_ja_enviado_hoje(
    tipo: str,
    cliente_key: str,
    data_ref: str,
) -> bool:
    """
    Verifica se um disparo do tipo/cliente já foi enviado com sucesso hoje.

    Args:
        tipo:        ex: 'alerta_roas', 'relatorio_diario', 'relatorio_semanal'
        cliente_key: ex: 'source_of', 'pod_kombucha'
        data_ref:    data de referência YYYY-MM-DD (apenas para contexto — checa ts_disparo de hoje)

    Returns:
        True se já existe registro ok=1 de hoje para esse tipo+cliente.
    """
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT id FROM slack_disparos_log
            WHERE tipo        = ?
              AND cliente_key = ?
              AND ok          = 1
              AND ts_disparo >= date('now', '-1 day')
            LIMIT 1
            """,
            (tipo, cliente_key),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def listar_disparos(
    tipo: str = None,
    cliente_key: str = None,
    limit: int = 50,
) -> list[dict]:
    """
    Lista disparos recentes com filtros opcionais.
    Útil para debug e auditoria via `/logs` no Slack.
    """
    conn = _conn()
    try:
        where, params = [], []
        if tipo:
            where.append("tipo = ?"); params.append(tipo)
        if cliente_key:
            where.append("cliente_key = ?"); params.append(cliente_key)
        sql = "SELECT * FROM slack_disparos_log"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts_disparo DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
