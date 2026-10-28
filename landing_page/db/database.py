"""
landing_page/db/database.py

Schema SQLite para o agente de Landing Pages.

Tabelas:
  projetos          — cada LP gerada (briefing completo + HTML)
  mensagens         — histórico de conversa por usuário
  preferencias      — preferências visuais persistentes por usuário
  refs_visuais      — referências boas/ruins por usuário (acumulativas)
  sessoes           — log de sessões abertas/fechadas
"""
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "landing_page.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
-- Migrations seguras (idempotentes)
CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY);

-- ── Projetos gerados ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS projetos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT    NOT NULL,
    criado_em       TEXT    NOT NULL DEFAULT (datetime('now')),

    -- Briefing
    cliente         TEXT    NOT NULL,
    objetivo        TEXT    NOT NULL,
    publico         TEXT    NOT NULL,
    tom             TEXT    NOT NULL,
    extras          TEXT,
    refs_boas       TEXT,
    refs_ruins      TEXT,

    -- Resultado
    arquivo_nome    TEXT,
    slack_channel   TEXT,
    slack_ts        TEXT,
    status          TEXT    NOT NULL DEFAULT 'gerado',  -- gerado | falha | cancelado | gerando
    observacao      TEXT,
    html_content    TEXT,                         -- HTML completo salvo para recuperação
    job_id          TEXT                          -- ID do job assíncrono (quando geração em background)
);

-- ── Histórico de mensagens por sessão ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mensagens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT    NOT NULL,
    sessao_id       TEXT    NOT NULL,
    criado_em       TEXT    NOT NULL DEFAULT (datetime('now')),
    papel           TEXT    NOT NULL CHECK(papel IN ('user','agent')),
    texto           TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_user ON mensagens(user_id, criado_em DESC);

-- ── Preferências visuais persistentes ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS preferencias (
    user_id         TEXT    PRIMARY KEY,
    atualizado_em   TEXT    NOT NULL DEFAULT (datetime('now')),

    tom_favorito    TEXT,           -- ex: "aspiracional/premium"
    cliente_padrao  TEXT,           -- ex: "Source Of"
    estilo_visual   TEXT,           -- descrição livre do estilo preferido
    notas           TEXT            -- anotações livres sobre o usuário
);

-- ── Referências visuais acumulativas ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS refs_visuais (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT    NOT NULL,
    criado_em       TEXT    NOT NULL DEFAULT (datetime('now')),
    tipo            TEXT    NOT NULL CHECK(tipo IN ('boa','ruim')),
    descricao       TEXT    NOT NULL,
    projeto_id      INTEGER REFERENCES projetos(id),
    ativo           INTEGER NOT NULL DEFAULT 1  -- 0 = descartada pelo usuário
);
CREATE INDEX IF NOT EXISTS idx_refs_user ON refs_visuais(user_id, tipo, ativo);

-- ── Log de sessões ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessoes (
    sessao_id       TEXT    PRIMARY KEY,
    user_id         TEXT    NOT NULL,
    iniciada_em     TEXT    NOT NULL DEFAULT (datetime('now')),
    encerrada_em    TEXT,
    step_final      TEXT,
    resultado       TEXT    CHECK(resultado IN ('gerado','cancelado','timeout',NULL))
);
"""


def get_conn() -> sqlite3.Connection:
    """Retorna conexão com o banco, criando schema se necessário."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ── Conexão global (singleton simples) ────────────────────────────────────────
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = get_conn()
        logger.info("[LP-DB] Banco inicializado em %s", DB_PATH)
    return _conn
