"""
utils/idempotencia.py
─────────────────────
Livro-razão de efeitos externos (post no Slack, card no Notion, envio a API): cada efeito tem uma
chave determinística e só acontece uma vez, mesmo com retry, timer "Persistent" que recupera
execução perdida, ou carga manual rodando junto com o timer.

    from utils.idempotencia import uma_vez
    with uma_vez(f"relatorio_diario:{cliente}:{plataforma}:{data}") as pode:
        if pode:
            postar_no_slack(...)        # exceção aqui libera a chave para a próxima tentativa

Semântica: a chave é reservada ANTES do efeito (INSERT em transação), então dois processos
concorrentes não executam o mesmo efeito. Falha → reserva apagada (pode tentar de novo).
Reserva órfã (processo morto no meio) expira após EXPIRA_MIN minutos.
ponytail: SQLite local; se os jobs saírem desta VM, trocar por tabela com a mesma chave no destino.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("IDEMPOTENCIA_DB",
                         Path(__file__).resolve().parent.parent / "data" / "idempotencia.db"))
EXPIRA_MIN = 60


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS efeitos (
                   chave      TEXT PRIMARY KEY,
                   status     TEXT NOT NULL CHECK (status IN ('em_andamento', 'feito')),
                   detalhe    TEXT,
                   atualizado TEXT NOT NULL)""")
    return c


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def ja_feito(chave: str) -> bool:
    with _conn() as c:
        return c.execute("SELECT 1 FROM efeitos WHERE chave=? AND status='feito'", (chave,)).fetchone() is not None


@contextmanager
def uma_vez(chave: str, detalhe: str = ""):
    """Rende True se este processo deve executar o efeito; False se já foi feito ou está em andamento."""
    c = _conn()
    try:
        limite = (datetime.now(timezone.utc) - timedelta(minutes=EXPIRA_MIN)).isoformat()
        c.execute("BEGIN IMMEDIATE")
        c.execute("DELETE FROM efeitos WHERE chave=? AND status='em_andamento' AND atualizado<?", (chave, limite))
        reservou = c.execute("INSERT OR IGNORE INTO efeitos VALUES (?, 'em_andamento', ?, ?)",
                             (chave, detalhe, _agora())).rowcount == 1
        c.execute("COMMIT")
        if not reservou:
            yield False
            return
        try:
            yield True
        except BaseException:
            c.execute("DELETE FROM efeitos WHERE chave=? AND status='em_andamento'", (chave,))
            raise
        c.execute("UPDATE efeitos SET status='feito', atualizado=? WHERE chave=?", (_agora(), chave))
    finally:
        c.close()


def marcar_feito(chave: str, detalhe: str = "") -> None:
    """Registra um efeito que aconteceu por outro caminho (ex.: importação do histórico)."""
    with _conn() as c:
        c.execute("INSERT INTO efeitos VALUES (?, 'feito', ?, ?) ON CONFLICT(chave) DO UPDATE SET status='feito'",
                  (chave, detalhe, _agora()))
