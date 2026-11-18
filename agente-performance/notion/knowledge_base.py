"""
notion/knowledge_base.py
────────────────────────
Memória de longo prazo do Agente de Performance Guará.

Fluxo:
  1. Toda resposta gerada é salva como PENDENTE (aprovado=0)
  2. Quando o usuário reage com 👍 no Slack, a entrada é APROVADA
  3. Antes de cada resposta do Claude, as top N entradas aprovadas para
     aquele intent/campanha são injetadas no system prompt como "memória"

Armazenamento: SQLite em data/knowledge_base.db (migrado do Notion em 11/2026, na saída da AWS).
O nome do módulo e a interface ficaram iguais para não mexer nos chamadores; "page_id" agora é o id da linha.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("KB_DB_PATH", Path(__file__).resolve().parent.parent / "data" / "knowledge_base.db"))

# Número máximo de entradas de contexto injetadas no prompt
MAX_CONTEXT_ENTRIES = 5

_lock = threading.Lock()  # ponytail: um lock global; volume é de uma escrita por resposta do bot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_entradas (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pergunta     TEXT NOT NULL,
    resposta     TEXT NOT NULL,
    intent       TEXT,
    campanha     TEXT,
    periodo      TEXT,
    aprovado     INTEGER NOT NULL DEFAULT 0,
    frequencia   INTEGER NOT NULL DEFAULT 1,
    aprovado_por TEXT,
    slack_ts     TEXT,
    data         TEXT NOT NULL,
    criado_em    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS kb_aprov_intent ON kb_entradas (aprovado, intent, frequencia DESC);
CREATE INDEX IF NOT EXISTS kb_slack_ts     ON kb_entradas (slack_ts);
"""


@contextmanager
def _conn():
    with _lock:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            c.executescript(_SCHEMA)
            yield c
            c.commit()
        finally:
            c.close()


# ── CRUD principal ────────────────────────────────────────────────────────────

def salvar_entrada(
    pergunta:   str,
    resposta:   str,
    intent:     str,
    campanha:   Optional[str] = None,
    data_ini:   Optional[str] = None,
    data_fim:   Optional[str] = None,
    slack_msg_ts: Optional[str] = None,
) -> Optional[str]:
    """Salva uma nova entrada (não aprovada). Retorna o id criado, ou None em caso de erro."""
    periodo = f"{data_ini} → {data_fim}" if data_ini and data_fim else (data_ini or "")
    try:
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO kb_entradas (pergunta, resposta, intent, campanha, periodo, slack_ts, data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pergunta, resposta, intent, campanha, periodo, slack_msg_ts, date.today().isoformat()),
            )
            logger.debug("KB: entrada salva %s (intent=%s)", cur.lastrowid, intent)
            return str(cur.lastrowid)
    except Exception as e:
        logger.warning("KB: exceção ao salvar: %s", e)
        return None


def aprovar_entrada(page_id: str, aprovado_por: str = "") -> bool:
    """Marca uma entrada como aprovada (👍 no Slack), incrementa frequência e registra quem aprovou."""
    try:
        with _conn() as c:
            ok = c.execute(
                "UPDATE kb_entradas SET aprovado = 1, frequencia = frequencia + 1, aprovado_por = ? WHERE id = ?",
                ((aprovado_por or "👍 Slack")[:200], int(page_id)),
            ).rowcount == 1
        if ok:
            logger.info("KB: entrada aprovada %s por %s", page_id, aprovado_por)
        else:
            logger.warning("KB: entrada não encontrada: %s", page_id)
        return ok
    except Exception as e:
        logger.warning("KB: exceção ao aprovar: %s", e)
        return False


def incrementar_frequencia(page_id: str) -> bool:
    """Incrementa o contador de frequência quando a mesma pergunta é feita novamente."""
    try:
        with _conn() as c:
            return c.execute("UPDATE kb_entradas SET frequencia = frequencia + 1 WHERE id = ?",
                             (int(page_id),)).rowcount == 1
    except Exception:
        return False


def _casa_campanha(campanha: Optional[str], da_entrada: Optional[str]) -> bool:
    # Mesmo fuzzy simples da versão Notion: substring nos dois sentidos; entrada sem campanha sempre casa.
    if not campanha or not da_entrada:
        return True
    a, b = campanha.lower(), da_entrada.lower()
    return a in b or b in a


def buscar_contexto(
    intent: Optional[str] = None,
    campanha: Optional[str] = None,
    limit: int = MAX_CONTEXT_ENTRIES,
) -> list[dict]:
    """Entradas aprovadas mais frequentes para um intent/campanha, para injetar no prompt do Claude."""
    sql = ("SELECT id, pergunta, resposta, intent, campanha, periodo, frequencia FROM kb_entradas"
           " WHERE aprovado = 1" + (" AND intent = ?" if intent else "") +
           " ORDER BY frequencia DESC, id DESC LIMIT 200")
    try:
        with _conn() as c:
            linhas = c.execute(sql, (intent,) if intent else ()).fetchall()
    except Exception as e:
        logger.warning("KB: exceção na busca de contexto: %s", e)
        return []
    res = [{"page_id": str(r["id"]), "pergunta": r["pergunta"], "resposta": r["resposta"],
            "intent": r["intent"], "campanha": r["campanha"] or "", "periodo": r["periodo"] or "",
            "frequencia": r["frequencia"]}
           for r in linhas if _casa_campanha(campanha, r["campanha"])]
    return res[:limit]


def buscar_por_slack_ts(slack_ts: str) -> Optional[str]:
    """id da entrada associada a uma mensagem do Slack (para o 👍)."""
    try:
        with _conn() as c:
            r = c.execute("SELECT id FROM kb_entradas WHERE slack_ts = ? ORDER BY id DESC LIMIT 1",
                          (slack_ts,)).fetchone()
        return str(r["id"]) if r else None
    except Exception:
        return None


def formatar_contexto_para_prompt(entradas: list[dict]) -> str:
    """Formata as entradas aprovadas como bloco de texto para injetar no prompt do Claude."""
    if not entradas:
        return ""

    linhas = [
        "## 📚 Memória — respostas aprovadas anteriormente\n",
        "Use estas respostas como referência de qualidade e estilo. "
        "Adapte ao contexto atual sem copiar literalmente.\n",
    ]
    for i, e in enumerate(entradas, 1):
        freq = e.get("frequencia", 1)
        camp = f" · campanha: {e['campanha']}" if e.get("campanha") else ""
        per  = f" · período: {e['periodo']}" if e.get("periodo") else ""
        linhas.append(f"\n### Exemplo {i} (perguntado {freq}x{camp}{per})")
        linhas.append(f"**Pergunta:** {e['pergunta']}")
        # Trunca resposta longa para não explodir o prompt
        resp = e['resposta'][:800] + "..." if len(e['resposta']) > 800 else e['resposta']
        linhas.append(f"**Resposta:** {resp}")

    return "\n".join(linhas)


# ── Listar entradas (admin) ───────────────────────────────────────────────────

def listar_entradas(apenas_aprovadas: bool = False, limit: int = 20) -> list[dict]:
    """Lista entradas para administração / debug."""
    sql = ("SELECT id, pergunta, intent, campanha, aprovado, frequencia, data FROM kb_entradas" +
           (" WHERE aprovado = 1" if apenas_aprovadas else "") + " ORDER BY frequencia DESC, id DESC LIMIT ?")
    try:
        with _conn() as c:
            return [{"page_id": str(r["id"]), "pergunta": r["pergunta"][:100], "intent": r["intent"],
                     "campanha": r["campanha"] or "", "aprovado": bool(r["aprovado"]),
                     "frequencia": r["frequencia"], "data": r["data"]}
                    for r in c.execute(sql, (limit,))]
    except Exception as e:
        logger.warning("KB listar_entradas erro: %s", e)
        return []
