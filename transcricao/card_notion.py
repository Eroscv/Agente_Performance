"""
card_notion.py
Criação de cards no Notion e consulta de status.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import requests

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR.parent / "agente-performance" / ".env")

from card_constants import USUARIOS_UNICOS, CLIENTES_LISTA, FRENTES_LISTA
from card_db import salvar_card, encerrar_sessao, invalidar_cache

logger = logging.getLogger(__name__)

NOTION_DB_ID   = "80b08108-7f14-4d79-b6dc-69e38c8de57d"
NOTION_VERSION = "2022-06-28"

STATUS_EMOJI = {
    "A fazer":      "🔵",
    "Em andamento": "🟡",
    "Em revisão":   "🟠",
    "Concluído":    "✅",
    "Bloqueado":    "🔴",
}


def _headers() -> dict:
    key = os.getenv("NOTION_API_KEY") or os.getenv("NOTION_KEY", "")
    return {
        "Authorization":  f"Bearer {key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type":   "application/json",
    }


def buscar_cards(responsavel_id: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Busca cards não concluídos. Filtra por responsável se informado."""
    filtro_feito = {"property": "Feito", "checkbox": {"equals": False}}
    if responsavel_id:
        payload_filter = {"and": [
            {"property": "Responsável atual", "people": {"contains": responsavel_id}},
            filtro_feito,
        ]}
    else:
        payload_filter = filtro_feito

    payload = {
        "page_size": limit,
        "filter":    payload_filter,
        "sorts":     [{"property": "Data", "direction": "ascending"}],
    }
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
        headers=_headers(), json=payload, timeout=15,
    )
    if resp.status_code != 200:
        logger.warning("[status-cards] Notion %s: %s", resp.status_code, resp.text[:120])
        return []

    cards = []
    for r in resp.json().get("results", []):
        props        = r.get("properties", {})
        titulo       = "".join(t["text"]["content"] for t in props.get("Tarefa", {}).get("title", []))
        status       = (props.get("Status", {}).get("select") or {}).get("name", "—")
        situacao     = (props.get("Situação", {}).get("formula") or {}).get("string", "")
        prazo        = (props.get("Data", {}).get("date") or {}).get("start", "—")
        clientes     = [c["name"] for c in props.get("Cliente", {}).get("multi_select", [])]
        area         = (props.get("Área", {}).get("select") or {}).get("name", "—")
        revisores    = props.get("Revisor", {}).get("people", [])
        revisor_nome = revisores[0].get("name", "—") if revisores else "—"
        resp_pessoas = props.get("Responsável atual", {}).get("people", [])
        resp_nome    = resp_pessoas[0].get("name", "—") if resp_pessoas else "—"
        cards.append({
            "titulo":      titulo,
            "status":      status,
            "situacao":    situacao,
            "prazo":       prazo,
            "cliente":     ", ".join(clientes) if clientes else "—",
            "area":        area,
            "revisor":     revisor_nome,
            "responsavel": resp_nome,
            "url":         r.get("url", ""),
        })
    return cards


def formatar_cards(cards: list[dict], mostrar_responsavel: bool = False) -> str:
    """Monta o texto formatado de uma lista de cards."""
    blocos = []
    for c in cards:
        emoji    = STATUS_EMOJI.get(c["status"], "⚪")
        situacao = f"  {c['situacao']}" if c["situacao"] and "Atrasad" in c["situacao"] else ""
        linhas   = [f"{emoji} *{c['titulo']}*"]
        linhas.append(f"   📌 Status: {c['status']}{situacao}")
        if c["prazo"] != "—":
            linhas.append(f"   📅 Prazo: {c['prazo']}")
        if c["cliente"] != "—":
            linhas.append(f"   🏢 Cliente: {c['cliente']}")
        if c["area"] != "—":
            linhas.append(f"   🗂️ Área: {c['area']}")
        if mostrar_responsavel and c["responsavel"] != "—":
            linhas.append(f"   👤 Responsável: {c['responsavel']}")
        if c["revisor"] != "—":
            linhas.append(f"   🔍 Revisor: {c['revisor']}")
        if c["url"]:
            linhas.append(f"   🔗 {c['url']}")
        blocos.append("\n".join(linhas))
    return "\n\n".join(blocos)


async def criar_no_notion(user_id: str, sess: dict, say):
    """Cria o card no Notion com os dados da sessão."""
    from criar_tarefas_notion import criar_card_notion
    import criar_tarefas_notion as _ctn

    titulo      = sess.get("titulo", "")
    responsavel = sess.get("responsavel", "")
    revisor     = sess.get("revisor", "")
    prazo       = sess.get("prazo", "")
    descricao   = sess.get("descricao", "")
    cliente     = sess.get("cliente", "")

    resp_id = USUARIOS_UNICOS.get(responsavel, "dab34a81-2f1b-4708-b930-b8f54da78e7f")
    rev_id  = USUARIOS_UNICOS.get(revisor,     "dab34a81-2f1b-4708-b930-b8f54da78e7f")

    tarefa = {
        "titulo":           titulo,
        "responsavel_id":   resp_id,
        "responsavel_nome": responsavel,
        "descricao":        descricao,
        "prazo_detectado":  prazo,
    }

    from card_constants import clear_session
    clear_session(user_id)
    await say(text="⏳ Criando card no Notion...")

    try:
        _old = dict(_ctn.REVISOR_PADRAO)
        _ctn.REVISOR_PADRAO["Performance"] = rev_id
        _ctn.REVISOR_PADRAO["3P"]          = rev_id
        _ctn.REVISOR_PADRAO["Gestão"]      = rev_id
        url = criar_card_notion(
            tarefa=tarefa, reuniao_data=prazo,
            reuniao_titulo="Manual (Slack)", fonte_id=f"slack_{user_id}",
            clientes=[cliente] if cliente and cliente != "Todos" else [],
            area="Performance",
        )
        _ctn.REVISOR_PADRAO.update(_old)
    except Exception as e:
        logger.exception("Erro ao criar card Notion: %s", e)
        await say(text=f"❌ Erro ao criar card: {e}")
        return

    if url:
        salvar_card(
            user_id=user_id, titulo=titulo, cliente=cliente,
            frente=sess.get("frente", ""), responsavel=responsavel,
            revisor=revisor, prazo=prazo, descricao=descricao,
            url_notion=url if isinstance(url, str) else "",
            status="criado",
        )
        encerrar_sessao(user_id, "criado")
        invalidar_cache()
        await say(text=(
            f"✅ *Card criado com sucesso!*\n"
            f"• *{titulo}*\n"
            f"👤 {responsavel}  |  📅 {prazo}  |  🏢 {cliente}\n"
            f"🔗 {url}"
        ))
    else:
        salvar_card(user_id=user_id, titulo=titulo, status="falha")
        encerrar_sessao(user_id, "falha")
        await say(text="❌ Falha ao criar o card no Notion. Tente novamente.")
