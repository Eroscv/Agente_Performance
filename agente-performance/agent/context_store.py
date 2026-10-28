"""
agent/context_store.py

Memória de contexto por thread do Slack.
Armazena histórico de conversas em SQLite para que o agente
entenda perguntas de acompanhamento como "e essa semana?" ou "compare com a ABO".

Melhorias (MELHORIA 3):
  - Contexto persistido também em `campanhas.db`, tabela `thread_context`
  - append_history() salva em SQLite (mantendo ram também)
  - build_context_summary() lê do SQLite (sobrevive a restart)
  - Compatibilidade total com chamadas existentes em slack/handlers.py

Schema SQLite (campanhas.db):
  thread_context (id, thread_ts, channel, role, content, meta_json, created_at)
  idx_thread_ts

Schema legado (context.db) mantido para compatibilidade — não é removido.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from agent.tz import agora_brt
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR    = Path(__file__).parent.parent
CTX_DB_PATH = BASE_DIR / "data" / "context.db"       # legado — mantido
DB_PATH     = BASE_DIR / "data" / "campanhas.db"      # MELHORIA 3: persistência real
HISTORY_LIMIT = 10       # máximo de turnos por thread
TTL_HOURS     = 24       # threads expiram após 24h de inatividade

_conn: Optional[sqlite3.Connection] = None

# ── LEGADO: context.db (mantido para não quebrar código que importa funções legacy) ──

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


# ── MELHORIA 3: persistência em campanhas.db ────────────────────────────────

_db_conn: Optional[sqlite3.Connection] = None


def _get_db_conn() -> sqlite3.Connection:
    """Conexão para campanhas.db — cria tabela thread_context se não existir."""
    global _db_conn
    if _db_conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _db_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _db_conn.row_factory = sqlite3.Row
        _db_conn.execute("PRAGMA journal_mode=WAL")
        try:
            _db_conn.executescript("""
                CREATE TABLE IF NOT EXISTS thread_context (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_ts   TEXT NOT NULL,
                    channel     TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    meta_json   TEXT,
                    created_at  TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_thread_ts ON thread_context(thread_ts);
            """)
            _db_conn.commit()
        except Exception as e:
            logger.warning("[context_store] Falha ao criar tabela thread_context em campanhas.db: %s", e)
    return _db_conn


# ── API pública (compatível com chamadas existentes) ─────────────────────────

def get_history(thread_ts: str) -> list[dict]:
    """
    Retorna histórico de uma thread.
    Lê do context.db legado (formato JSON blob).
    Lista vazia se não existir ou expirada.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT history, updated_at FROM thread_context WHERE thread_ts = ?",
        (thread_ts,)
    ).fetchone()

    if not row:
        return []

    # Verifica TTL
    updated = datetime.fromisoformat(row["updated_at"])
    if agora_brt() - updated > timedelta(hours=TTL_HOURS):
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

    MELHORIA 3: salva em AMBOS context.db (legado) E campanhas.db (persistente).
    """
    # ── Salva no context.db legado (JSON blob) ──────────────────────────────
    try:
        conn    = _get_conn()
        history = get_history(thread_ts)

        history.append({
            "role": role,
            "text": text,
            "meta": meta or {},
            "ts":   agora_brt().isoformat(),
        })

        if len(history) > HISTORY_LIMIT:
            history = history[-HISTORY_LIMIT:]

        conn.execute("""
            INSERT INTO thread_context (thread_ts, channel, history, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(thread_ts) DO UPDATE SET
                history    = excluded.history,
                updated_at = excluded.updated_at
        """, (thread_ts, channel, json.dumps(history, ensure_ascii=False), agora_brt().isoformat()))
        conn.commit()
    except Exception as e:
        logger.warning("[context_store] Falha ao salvar em context.db: %s", e)

    # ── MELHORIA 3: salva em campanhas.db (linha individual) ────────────────
    try:
        db   = _get_db_conn()
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        db.execute("""
            INSERT INTO thread_context (thread_ts, channel, role, content, meta_json)
            VALUES (?, ?, ?, ?, ?)
        """, (thread_ts, channel or "", role, text, meta_json))
        db.commit()
    except Exception as e:
        logger.warning("[context_store] Falha ao salvar em campanhas.db: %s", e)


def delete_history(thread_ts: str):
    """Remove histórico de uma thread (ambos os bancos)."""
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM thread_context WHERE thread_ts = ?", (thread_ts,))
        conn.commit()
    except Exception as e:
        logger.debug("[context_store] delete legado: %s", e)

    try:
        db = _get_db_conn()
        db.execute("DELETE FROM thread_context WHERE thread_ts = ?", (thread_ts,))
        db.commit()
    except Exception as e:
        logger.debug("[context_store] delete campanhas.db: %s", e)


def cleanup_expired():
    """Remove threads expiradas (> TTL_HOURS) de ambos os bancos. Chamar periodicamente."""
    # Legado context.db
    try:
        conn     = _get_conn()
        cutoff   = (agora_brt() - timedelta(hours=TTL_HOURS)).isoformat()
        deleted  = conn.execute(
            "DELETE FROM thread_context WHERE updated_at < ?", (cutoff,)
        ).rowcount
        conn.commit()
        if deleted:
            logger.info("Context store (legado): %d threads expiradas removidas", deleted)
    except Exception as e:
        logger.debug("[context_store] cleanup legado: %s", e)

    # campanhas.db
    try:
        db = _get_db_conn()
        cutoff_sql = f"-{TTL_HOURS} hours"
        deleted2 = db.execute(
            "DELETE FROM thread_context WHERE created_at < datetime('now', ?)",
            (cutoff_sql,)
        ).rowcount
        db.commit()
        if deleted2:
            logger.info("Context store (campanhas.db): %d entradas expiradas removidas", deleted2)
    except Exception as e:
        logger.debug("[context_store] cleanup campanhas.db: %s", e)


def build_context_summary(thread_ts: str) -> str:
    """
    Monta um resumo textual do histórico da thread para injetar no prompt do Claude.

    MELHORIA 3: Lê primariamente do campanhas.db (persistente entre restarts).
    Fallback para context.db legado se campanhas.db não tiver dados.
    Retorna string vazia se não houver histórico.
    """
    # Tenta primeiro no campanhas.db (MELHORIA 3)
    history_from_db: list[dict] = []
    try:
        db = _get_db_conn()
        cutoff_sql = f"-{TTL_HOURS} hours"
        rows = db.execute("""
            SELECT role, content, meta_json, created_at
            FROM thread_context
            WHERE thread_ts = ?
              AND created_at >= datetime('now', ?)
            ORDER BY id DESC
            LIMIT 10
        """, (thread_ts, cutoff_sql)).fetchall()
        # Inverte para ordem cronológica
        for row in reversed(rows):
            try:
                meta = json.loads(row["meta_json"] or "{}")
            except Exception:
                meta = {}
            history_from_db.append({
                "role":    row["role"],
                "text":    row["content"],
                "meta":    meta,
                "ts":      row["created_at"],
            })
    except Exception as e:
        logger.debug("[context_store] build_context_summary campanhas.db: %s", e)

    # Usa campanhas.db se tiver dados; senão usa context.db legado
    history = history_from_db if history_from_db else get_history(thread_ts)

    if not history:
        return ""

    # Extrai contexto estruturado da última resposta do agente (para herança de cliente/período)
    ultimo_meta: dict = {}
    for msg in reversed(history):
        if msg["role"] == "agent" and msg.get("meta"):
            ultimo_meta = msg["meta"]
            break

    linhas = ["Histórico da conversa (thread atual):"]

    # Linha de contexto estruturado — usada pelo NLP para herdar cliente/plataforma/período
    ctx_parts = []
    if ultimo_meta.get("cliente_key") or ultimo_meta.get("cliente"):
        ck = ultimo_meta.get("cliente_key") or ultimo_meta.get("cliente")
        ctx_parts.append(f"cliente={ck}")
    if ultimo_meta.get("plataforma"):
        ctx_parts.append(f"plataforma={ultimo_meta['plataforma']}")
    if ultimo_meta.get("data_inicio"):
        ctx_parts.append(f"período={ultimo_meta['data_inicio']} a {ultimo_meta.get('data_fim', '?')}")
    if ctx_parts:
        linhas.append(f"  [contexto ativo: {', '.join(ctx_parts)}]")

    for msg in history[-6:]:  # últimos 6 turnos
        role  = "Usuário" if msg["role"] == "user" else "Agente"
        texto = msg["text"][:200]
        meta  = msg.get("meta", {})

        linha = f"  [{role}]: {texto}"

        if msg["role"] == "agent" and meta.get("campanha"):
            linha += f" (campanha: {meta['campanha']})"
        if msg["role"] == "agent" and meta.get("data_inicio"):
            linha += f" (período: {meta['data_inicio']} a {meta.get('data_fim', '?')})"

        linhas.append(linha)

    return "\n".join(linhas)
