"""
db/cron_log.py

Histórico de execuções do cron de relatórios.
Tabela SQLite: cron_runs

Permite saber:
  - Qual relatório foi gerado com sucesso
  - Se o Slack foi postado
  - Se houve erro e qual foi
  - Quando foi gerado

Uso:
    from db.cron_log import registrar_run, listar_runs, ultimo_run_sucesso
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
# DB roteado para data/logs/outbox.db via db.connections
from db.connections import get_outbox_conn as _get_db_conn

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cron_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_key  TEXT    NOT NULL,
    periodo      TEXT    NOT NULL,
    data_inicio  TEXT    NOT NULL,
    data_fim     TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'iniciado',  -- iniciado | sucesso | erro
    notion_url   TEXT,
    slack_ok     INTEGER DEFAULT 0,
    erro         TEXT,
    criado_em    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    atualizado_em TEXT
);

CREATE INDEX IF NOT EXISTS idx_cron_runs_cliente  ON cron_runs(cliente_key);
CREATE INDEX IF NOT EXISTS idx_cron_runs_status   ON cron_runs(status);
CREATE INDEX IF NOT EXISTS idx_cron_runs_criado   ON cron_runs(criado_em);
"""


_tables_ensured = False

def _conn() -> sqlite3.Connection:
    global _tables_ensured
    conn = _get_db_conn()
    if not _tables_ensured:
        for _stmt in [s.strip() for s in CREATE_TABLE.split(";") if s.strip()]:
            try:
                conn.execute(_stmt)
            except Exception:
                pass
        _tables_ensured = True
    return conn


def registrar_run(
    cliente_key: str,
    periodo: str,
    data_inicio: str,
    data_fim: str,
    status: str = "iniciado",
    notion_url: str = None,
    slack_ok: bool = False,
    erro: str = None,
    run_id: int = None,
) -> int:
    """
    Insere ou atualiza um run no histórico.

    Se run_id for fornecido, atualiza o registro existente.
    Caso contrário, insere novo e retorna o id gerado.
    """
    conn = _conn()
    agora = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    if run_id:
        conn.execute("""
            UPDATE cron_runs SET
                status        = ?,
                notion_url    = COALESCE(?, notion_url),
                slack_ok      = ?,
                erro          = ?,
                atualizado_em = ?
            WHERE id = ?
        """, (status, notion_url, int(slack_ok), erro, agora, run_id))
        conn.commit()
        return run_id
    else:
        cur = conn.execute("""
            INSERT INTO cron_runs
                (cliente_key, periodo, data_inicio, data_fim, status, notion_url, slack_ok, erro, criado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cliente_key, periodo, data_inicio, data_fim, status, notion_url, int(slack_ok), erro, agora))
        conn.commit()
        new_id = cur.lastrowid
        logger.debug("[CronLog] Run %d registrado: %s / %s / %s", new_id, cliente_key, periodo, status)
        return new_id


def listar_runs(
    cliente_key: str = None,
    periodo: str = None,
    status: str = None,
    limit: int = 50,
) -> list[dict]:
    """Lista runs com filtros opcionais."""
    conn = _conn()
    where = []
    params = []
    if cliente_key:
        where.append("cliente_key = ?")
        params.append(cliente_key)
    if periodo:
        where.append("periodo = ?")
        params.append(periodo)
    if status:
        where.append("status = ?")
        params.append(status)

    sql = "SELECT * FROM cron_runs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY criado_em DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def ultimo_run_sucesso(cliente_key: str, periodo: str) -> Optional[dict]:
    """Retorna o último run bem-sucedido de um cliente/período."""
    conn = _conn()
    row = conn.execute("""
        SELECT * FROM cron_runs
        WHERE cliente_key = ? AND periodo = ? AND status = 'sucesso'
        ORDER BY criado_em DESC LIMIT 1
    """, (cliente_key, periodo)).fetchone()
    return dict(row) if row else None


def resumo_runs(dias: int = 30) -> list[dict]:
    """
    Resumo de execuções dos últimos N dias.
    Útil para o /status do Slack.
    """
    conn = _conn()
    rows = conn.execute("""
        SELECT
            cliente_key,
            periodo,
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'sucesso' THEN 1 ELSE 0 END) AS sucessos,
            SUM(CASE WHEN status = 'erro'    THEN 1 ELSE 0 END) AS erros,
            MAX(CASE WHEN status = 'sucesso' THEN criado_em END) AS ultimo_sucesso
        FROM cron_runs
        WHERE criado_em >= datetime('now', ?)
        GROUP BY cliente_key, periodo
        ORDER BY cliente_key, periodo
    """, (f"-{dias} days",)).fetchall()
    return [dict(r) for r in rows]
