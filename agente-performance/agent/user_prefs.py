"""
agent/user_prefs.py

Memória de preferências por usuário.
Aprende com o histórico de consultas para personalizar respostas.

Preferências inferidas:
  - periodo_default: período mais usado pelo usuário (semana/mes/hoje)
  - cliente_preferido: source_of ou pod_kombucha (se sempre pergunta sobre um)
  - intent_frequente: intent mais usada
  - ultima_consulta: timestamp da última consulta

Armazenado em campanhas.db, tabela user_prefs.
"""
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "campanhas.db"


def _get_conn():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table():
    """Cria tabela user_prefs se não existir."""
    try:
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_prefs (
                user_id         TEXT PRIMARY KEY,
                periodo_default TEXT,          -- 'semana', 'mes', 'hoje', null
                cliente_pref    TEXT,          -- 'source_of', 'pod_kombucha', null
                intent_freq     TEXT,          -- intent mais usada
                consultas_total INTEGER DEFAULT 0,
                ultima_consulta TEXT,
                atualizado_em   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[user_prefs] _ensure_table: %s", e)


_ensure_table()


def get_prefs(user_id: str) -> dict:
    """Retorna preferências do usuário. Dict vazio se não encontrado."""
    if not user_id:
        return {}
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM user_prefs WHERE user_id = ?", (user_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception as e:
        logger.debug("[user_prefs] get_prefs: %s", e)
        return {}


def atualizar_prefs(
    user_id: str,
    intent: str,
    data_inicio: Optional[str],
    data_fim: Optional[str],
    cliente_key: Optional[str],
) -> None:
    """
    Atualiza preferências do usuário com base na consulta atual.
    Chamado após cada consulta bem-sucedida.
    """
    if not user_id:
        return
    try:
        from datetime import date, timedelta

        # Inferir período da consulta
        periodo = None
        if data_inicio and data_fim:
            try:
                ini = date.fromisoformat(data_inicio)
                fim = date.fromisoformat(data_fim)
                delta = (fim - ini).days
                hoje = hoje_brt()
                if data_fim == str(hoje) or data_fim == str(hoje - timedelta(days=1)):
                    if delta <= 1:
                        periodo = "hoje"
                    elif delta <= 8:
                        periodo = "semana"
                    elif delta <= 32:
                        periodo = "mes"
            except Exception:
                pass

        conn = _get_conn()

        # Buscar prefs atuais
        row = conn.execute(
            "SELECT * FROM user_prefs WHERE user_id = ?", (user_id,)
        ).fetchone()

        if not row:
            conn.execute("""
                INSERT INTO user_prefs (user_id, periodo_default, cliente_pref, intent_freq, consultas_total, ultima_consulta)
                VALUES (?, ?, ?, ?, 1, datetime('now'))
            """, (user_id, periodo, cliente_key, intent))
        else:
            # Atualizar: período e cliente só mudam se aparecerem em >60% das últimas consultas
            # Simplificado: atualizar sempre que aparecer (média móvel implícita)
            novo_periodo = periodo if periodo else row["periodo_default"]
            novo_cliente = cliente_key if cliente_key else row["cliente_pref"]
            conn.execute("""
                UPDATE user_prefs SET
                    periodo_default = ?,
                    cliente_pref    = ?,
                    intent_freq     = ?,
                    consultas_total = consultas_total + 1,
                    ultima_consulta = datetime('now'),
                    atualizado_em   = datetime('now')
                WHERE user_id = ?
            """, (novo_periodo, novo_cliente, intent, user_id))

        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[user_prefs] atualizar_prefs: %s", e)


def aplicar_prefs(
    user_id: str,
    data_inicio: Optional[str],
    data_fim: Optional[str],
) -> "tuple[Optional[str], Optional[str]]":
    """
    Se o usuário não especificou período, aplica o período preferido dele.
    Retorna (data_inicio, data_fim) possivelmente ajustados.
    """
    if data_inicio or data_fim:
        return data_inicio, data_fim  # usuário especificou — não sobrescrever

    prefs = get_prefs(user_id)
    if not prefs or not prefs.get("periodo_default"):
        return data_inicio, data_fim

    from datetime import date, timedelta
    hoje = hoje_brt()
    periodo = prefs["periodo_default"]

    if periodo == "hoje":
        ontem = hoje - timedelta(days=1)
        return str(ontem), str(ontem)
    elif periodo == "semana":
        seg = hoje - timedelta(days=hoje.weekday() + 7)
        return str(seg), str(seg + timedelta(days=6))
    elif periodo == "mes":
        primeiro = hoje.replace(day=1) - timedelta(days=1)
        return str(primeiro.replace(day=1)), str(primeiro)

    return data_inicio, data_fim
