"""
landing_page/db/queries.py

Funções de acesso ao banco para o agente de Landing Pages.
"""
import uuid
import logging
from datetime import datetime
from typing import Optional
from .database import conn

logger = logging.getLogger(__name__)


# ── Sessões ───────────────────────────────────────────────────────────────────

def abrir_sessao(user_id: str) -> str:
    """Cria e retorna um novo sessao_id."""
    sid = str(uuid.uuid4())
    conn().execute(
        "INSERT INTO sessoes (sessao_id, user_id) VALUES (?, ?)",
        (sid, user_id),
    )
    conn().commit()
    return sid


def encerrar_sessao(sessao_id: str, step_final: str, resultado: str) -> None:
    conn().execute(
        "UPDATE sessoes SET encerrada_em=datetime('now'), step_final=?, resultado=? WHERE sessao_id=?",
        (step_final, resultado, sessao_id),
    )
    conn().commit()


# ── Histórico de mensagens ─────────────────────────────────────────────────────

def salvar_mensagem(user_id: str, sessao_id: str, papel: str, texto: str) -> None:
    conn().execute(
        "INSERT INTO mensagens (user_id, sessao_id, papel, texto) VALUES (?, ?, ?, ?)",
        (user_id, sessao_id, papel, texto),
    )
    conn().commit()


def historico_recente(user_id: str, limite: int = 20) -> list[dict]:
    rows = conn().execute(
        "SELECT criado_em, papel, texto FROM mensagens WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limite),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ── Projetos ──────────────────────────────────────────────────────────────────

def salvar_projeto(
    user_id: str,
    cliente: str, objetivo: str, publico: str, tom: str,
    extras: str = "", refs_boas: str = "", refs_ruins: str = "",
    arquivo_nome: str = "", slack_channel: str = "", slack_ts: str = "",
    status: str = "gerado", observacao: str = "",
) -> int:
    """Insere um projeto e retorna o id."""
    cur = conn().execute(
        """INSERT INTO projetos
           (user_id, cliente, objetivo, publico, tom, extras, refs_boas, refs_ruins,
            arquivo_nome, slack_channel, slack_ts, status, observacao)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (user_id, cliente, objetivo, publico, tom, extras, refs_boas, refs_ruins,
         arquivo_nome, slack_channel, slack_ts, status, observacao),
    )
    conn().commit()
    return cur.lastrowid


def listar_projetos(user_id: str, limite: int = 10) -> list[dict]:
    rows = conn().execute(
        """SELECT id, criado_em, cliente, objetivo, tom, status, arquivo_nome
           FROM projetos WHERE user_id=? ORDER BY criado_em DESC LIMIT ?""",
        (user_id, limite),
    ).fetchall()
    return [dict(r) for r in rows]


def buscar_projeto(projeto_id: int) -> Optional[dict]:
    row = conn().execute("SELECT * FROM projetos WHERE id=?", (projeto_id,)).fetchone()
    return dict(row) if row else None


# ── Preferências ──────────────────────────────────────────────────────────────

def get_preferencias(user_id: str) -> dict:
    row = conn().execute("SELECT * FROM preferencias WHERE user_id=?", (user_id,)).fetchone()
    return dict(row) if row else {}


def upsert_preferencias(user_id: str, **campos) -> None:
    """Atualiza campos de preferência (cria linha se não existir)."""
    pref = get_preferencias(user_id)
    if not pref:
        conn().execute("INSERT INTO preferencias (user_id) VALUES (?)", (user_id,))
    sets = ", ".join(f"{k}=?" for k in campos)
    sets += ", atualizado_em=datetime('now')"
    conn().execute(
        f"UPDATE preferencias SET {sets} WHERE user_id=?",
        (*campos.values(), user_id),
    )
    conn().commit()


# ── Referências visuais ───────────────────────────────────────────────────────

def salvar_ref(user_id: str, tipo: str, descricao: str,
               projeto_id: Optional[int] = None) -> None:
    """tipo: 'boa' ou 'ruim'."""
    conn().execute(
        "INSERT INTO refs_visuais (user_id, tipo, descricao, projeto_id) VALUES (?,?,?,?)",
        (user_id, tipo, descricao, projeto_id),
    )
    conn().commit()


def get_refs(user_id: str, tipo: Optional[str] = None, limite: int = 20) -> list[dict]:
    """Retorna refs ativas. tipo='boa'/'ruim' ou None para todas."""
    if tipo:
        rows = conn().execute(
            "SELECT id, criado_em, tipo, descricao FROM refs_visuais "
            "WHERE user_id=? AND tipo=? AND ativo=1 ORDER BY criado_em DESC LIMIT ?",
            (user_id, tipo, limite),
        ).fetchall()
    else:
        rows = conn().execute(
            "SELECT id, criado_em, tipo, descricao FROM refs_visuais "
            "WHERE user_id=? AND ativo=1 ORDER BY criado_em DESC LIMIT ?",
            (user_id, limite),
        ).fetchall()
    return [dict(r) for r in rows]


def refs_como_texto(user_id: str) -> tuple[str, str]:
    """Retorna (refs_boas_str, refs_ruins_str) para usar no prompt."""
    boas  = get_refs(user_id, "boa",  limite=5)
    ruins = get_refs(user_id, "ruim", limite=5)
    boas_str  = " | ".join(r["descricao"] for r in boas)  if boas  else ""
    ruins_str = " | ".join(r["descricao"] for r in ruins) if ruins else ""
    return boas_str, ruins_str


# ── Recuperação de HTML ───────────────────────────────────────────────────────

def salvar_html(projeto_id: int, html: str) -> None:
    conn().execute(
        "UPDATE projetos SET html_content=? WHERE id=?",
        (html, projeto_id),
    )
    conn().commit()


def get_html(projeto_id: int) -> str | None:
    row = conn().execute(
        "SELECT html_content FROM projetos WHERE id=?", (projeto_id,)
    ).fetchone()
    return row["html_content"] if row else None


def atualizar_status_projeto(projeto_id: int, status: str, observacao: str = "") -> None:
    conn().execute(
        "UPDATE projetos SET status=?, observacao=? WHERE id=?",
        (status, observacao, projeto_id),
    )
    conn().commit()


# ── Gestão de refs visuais ────────────────────────────────────────────────────

def desativar_ref(ref_id: int, user_id: str) -> bool:
    """Desativa (soft delete) uma ref. Retorna True se encontrou e desativou."""
    cur = conn().execute(
        "UPDATE refs_visuais SET ativo=0 WHERE id=? AND user_id=?",
        (ref_id, user_id),
    )
    conn().commit()
    return cur.rowcount > 0
