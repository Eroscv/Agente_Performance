"""
db/sync_alerts.py

Alertas Slack quando sync falha silenciosamente.

Regras:
  - Não envia alerta em cada tentativa — acumula falhas consecutivas
  - Envia quando: N falhas seguidas (padrão: 2) OU falha após sucesso anterior
  - Cooldown de 1h entre alertas do mesmo tipo (evita flood)
  - Tabela SQLite: sync_alerts (cliente_key, plataforma, falhas_consec, ultimo_alerta, ultimo_status)

API pública:
  registrar_resultado(cliente_key, plataforma, sucesso, erro=None)
      → chama _verificar_e_alertar() internamente
  reset(cliente_key, plataforma)
      → chamado ao retomar sucesso (limpa falhas consecutivas)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR  = Path(__file__).parent.parent
ALERTS_DB = BASE_DIR / "data" / "logs" / "sync_alerts.db"

FALHAS_PARA_ALERTAR = int(os.getenv("SYNC_ALERT_AFTER_FAILURES", "2"))
COOLDOWN_SEGUNDOS   = int(os.getenv("SYNC_ALERT_COOLDOWN_S", "3600"))   # 1h

_conn: Optional[sqlite3.Connection] = None


def _get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        ALERTS_DB.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(ALERTS_DB), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_alerts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_key     TEXT NOT NULL,
                plataforma      TEXT NOT NULL,
                falhas_consec   INTEGER NOT NULL DEFAULT 0,
                ultimo_status   TEXT,
                ultimo_erro     TEXT,
                ultimo_alerta   REAL DEFAULT 0,
                atualizado_em   REAL NOT NULL DEFAULT 0,
                UNIQUE(cliente_key, plataforma)
            )
        """)
        _conn.commit()
    return _conn


def _get_state(db: sqlite3.Connection, cliente_key: str, plataforma: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM sync_alerts WHERE cliente_key=? AND plataforma=?",
        (cliente_key, plataforma),
    ).fetchone()
    if not row:
        db.execute(
            "INSERT OR IGNORE INTO sync_alerts (cliente_key, plataforma, falhas_consec, ultimo_alerta, atualizado_em) "
            "VALUES (?, ?, 0, 0, ?)",
            (cliente_key, plataforma, time.time()),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM sync_alerts WHERE cliente_key=? AND plataforma=?",
            (cliente_key, plataforma),
        ).fetchone()
    return row


def _enviar_alerta_slack(cliente_key: str, plataforma: str, erro: str, falhas: int) -> None:
    """Envia mensagem de alerta no Slack."""
    try:
        import requests as _req
        token   = os.getenv("SLACK_BOT_TOKEN", "")
        channel = os.getenv("SLACK_ALERT_CHANNEL") or os.getenv("SLACK_CHANNEL", "performance")
        if not token:
            logger.warning("[SyncAlert] SLACK_BOT_TOKEN não configurado")
            return

        plural = "falha" if falhas == 1 else "falhas"
        texto = (
            f":warning: *Sync falhou — {cliente_key} / {plataforma}*\n"
            f"*{falhas} {plural} consecutiva(s)*\n"
            f"Erro: `{erro[:300]}`\n"
            f"_Verifique com_ `journalctl -u agente-guara -n 50`"
        )
        resp = _req.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json={"channel": channel, "text": texto, "mrkdwn": True},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            logger.info("[SyncAlert] ✅ Alerta enviado: %s/%s (%d falhas)", cliente_key, plataforma, falhas)
        else:
            logger.warning("[SyncAlert] Slack recusou: %s", data.get("error"))
    except Exception as exc:
        logger.warning("[SyncAlert] Falha ao enviar alerta Slack: %s", exc)


def registrar_resultado(
    cliente_key: str,
    plataforma: str,
    sucesso: bool,
    erro: Optional[str] = None,
) -> None:
    """
    Registra resultado de um sync e dispara alerta Slack se necessário.

    Args:
        cliente_key: ex 'source_of'
        plataforma:  'meta' | 'google' | 'shopify'
        sucesso:     True se sync ok, False se falhou
        erro:        mensagem de erro (só quando sucesso=False)
    """
    try:
        db    = _get_db()
        agora = time.time()
        state = _get_state(db, cliente_key, plataforma)

        if sucesso:
            # Retomada após falha → loga mas não alerta
            if state["falhas_consec"] > 0:
                logger.info(
                    "[SyncAlert] ✅ %s/%s retomou após %d falha(s)",
                    cliente_key, plataforma, state["falhas_consec"],
                )
            db.execute(
                "UPDATE sync_alerts SET falhas_consec=0, ultimo_status='ok', ultimo_erro=NULL, atualizado_em=? "
                "WHERE cliente_key=? AND plataforma=?",
                (agora, cliente_key, plataforma),
            )
            db.commit()
            return

        # Falha — incrementa contador
        novas_falhas = state["falhas_consec"] + 1
        db.execute(
            "UPDATE sync_alerts SET falhas_consec=?, ultimo_status='erro', ultimo_erro=?, atualizado_em=? "
            "WHERE cliente_key=? AND plataforma=?",
            (novas_falhas, (erro or "")[:500], agora, cliente_key, plataforma),
        )
        db.commit()

        # Verifica se deve alertar
        tempo_desde_ultimo = agora - (state["ultimo_alerta"] or 0)
        deve_alertar = (
            novas_falhas >= FALHAS_PARA_ALERTAR
            and tempo_desde_ultimo >= COOLDOWN_SEGUNDOS
        )

        if deve_alertar:
            _enviar_alerta_slack(cliente_key, plataforma, erro or "erro desconhecido", novas_falhas)
            db.execute(
                "UPDATE sync_alerts SET ultimo_alerta=? WHERE cliente_key=? AND plataforma=?",
                (agora, cliente_key, plataforma),
            )
            db.commit()
        else:
            logger.warning(
                "[SyncAlert] %s/%s — falha %d/%d (cooldown: %.0fs restantes)",
                cliente_key, plataforma, novas_falhas, FALHAS_PARA_ALERTAR,
                max(0, COOLDOWN_SEGUNDOS - tempo_desde_ultimo),
            )

    except Exception as exc:
        logger.debug("[SyncAlert] erro interno: %s", exc)


def status() -> list[dict]:
    """Retorna estado atual de todos os sync monitorados."""
    try:
        db = _get_db()
        rows = db.execute("SELECT * FROM sync_alerts ORDER BY cliente_key, plataforma").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
