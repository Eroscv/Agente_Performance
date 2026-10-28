"""
card_editar.py
Comando /editar-card — corrigir campos de um card já criado (prazo, revisor, responsável, descrição).

Fluxo:
  /editar-card           → lista últimos cards do usuário para escolher
  /editar-card [título]  → busca e confirma qual card editar
  Então pergunta qual campo alterar e aplica no Notion + atualiza DB local.
"""
import logging
import re
from datetime import timedelta, datetime
from typing import Optional

from card_constants import get_session, clear_session, USUARIOS_UNICOS, _USUARIOS_STR, _sessions
from card_helpers import parse_prazo, match_usuario
from card_db import listar_cards, salvar_card, invalidar_cache

logger = logging.getLogger(__name__)

_CAMPOS = {
    "1": "prazo",
    "2": "responsavel",
    "3": "revisor",
    "4": "descricao",
    "prazo": "prazo",
    "responsavel": "responsavel",
    "revisor": "revisor",
    "descrição": "descricao",
    "descricao": "descricao",
}

_CAMPOS_LABEL = {
    "prazo":       "📅 Prazo",
    "responsavel": "👤 Responsável",
    "revisor":     "🔍 Revisor",
    "descricao":   "📝 Descrição",
}


async def cmd_editar_card(ack, command, say):
    await ack()
    user_id   = command["user_id"]
    channel   = command["channel_id"]
    thread_ts = command.get("thread_ts")
    arg       = command.get("text", "").strip()

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        await say(**kwargs)

    cards = listar_cards(user_id=user_id, limite=10)
    if not cards:
        await _say(text="📭 Você ainda não criou nenhum card via Slack para editar.")
        return

    if arg:
        # Busca por título parcial
        matches = [c for c in cards if arg.lower() in c["titulo"].lower()]
        if not matches:
            matches = cards  # mostra todos se não achou
        else:
            cards = matches

    if len(cards) == 1:
        card = cards[0]
        _iniciar_edicao(user_id, card)
        await _say(text=_menu_edicao(card))
        return

    # Mostra lista para escolher
    get_session(user_id).update({"step": "editar_escolher", "channel": channel, "thread_ts": thread_ts})
    opcoes = "\n".join(
        f"  *{i+1}.* {c['titulo']} _(criado {c['criado_em'][:10]})_"
        for i, c in enumerate(cards[:8])
    )
    await _say(text=(
        f"✏️ *Editar card — escolha um:*\n\n{opcoes}\n\n"
        "Responda com o número."
    ))
    # Guarda lista na sessão para lookup
    get_session(user_id)["_cards_lista"] = cards[:8]


def _iniciar_edicao(user_id: str, card: dict) -> None:
    sess = get_session(user_id)
    sess.update({
        "step":           "editar_campo",
        "editar_card_id": card["id"],
        "editar_titulo":  card["titulo"],
        "editar_url":     card.get("url_notion", ""),
        "editar_notion_id": _extrair_notion_id(card.get("url_notion", "")),
    })


def _menu_edicao(card: dict) -> str:
    return (
        f"✏️ *Editando:* _{card['titulo']}_\n\n"
        f"Qual campo quer alterar?\n"
        f"  1️⃣ Prazo (atual: {card.get('prazo') or '—'})\n"
        f"  2️⃣ Responsável (atual: {card.get('responsavel') or '—'})\n"
        f"  3️⃣ Revisor (atual: {card.get('revisor') or '—'})\n"
        f"  4️⃣ Descrição\n\n"
        "Responda com o número ou nome do campo. `cancelar` para sair."
    )


def _extrair_notion_id(url: str) -> Optional[str]:
    """Extrai o ID do Notion de uma URL como https://notion.so/TITULO-abc123def"""
    if not url:
        return None
    m = re.search(r"([0-9a-f]{32})", url.replace("-", ""))
    if m:
        raw = m.group(1)
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return None


async def handle_message_editar_card(user_id: str, text: str, say, channel: str,
                                      thread_ts: Optional[str] = None) -> bool:
    """Retorna True se a mensagem foi consumida pelo fluxo de edição."""
    sess = _sessions.get(user_id)
    if not sess or not sess.get("step", "").startswith("editar"):
        return False

    step = sess["step"]
    text = text.strip()
    effective_ts = thread_ts or sess.get("thread_ts")

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if effective_ts:
            kwargs.setdefault("thread_ts", effective_ts)
        await say(**kwargs)

    if text.lower() in ("cancelar", "cancel", "sair"):
        clear_session(user_id)
        await _say(text="🚫 Edição cancelada.")
        return True

    # Step 1: usuário escolheu qual card da lista
    if step == "editar_escolher":
        cards = sess.get("_cards_lista", [])
        try:
            idx = int(text) - 1
            if 0 <= idx < len(cards):
                card = cards[idx]
                _iniciar_edicao(user_id, card)
                await _say(text=_menu_edicao(card))
                return True
        except ValueError:
            pass
        await _say(text="⚠️ Número inválido. Responda com o número da lista ou `cancelar`.")
        return True

    # Step 2: usuário escolheu qual campo editar
    if step == "editar_campo":
        campo = _CAMPOS.get(text.lower())
        if not campo:
            await _say(text="⚠️ Campo não reconhecido. Use `1` prazo, `2` responsável, `3` revisor, `4` descrição, ou `cancelar`.")
            return True
        sess["step"]         = f"editar_valor_{campo}"
        sess["editar_campo"] = campo
        label = _CAMPOS_LABEL[campo]

        if campo == "prazo":
            await _say(text=(
                f"📅 *Novo prazo para* _{sess['editar_titulo']}_\n"
                "Ex: `2026-11-15`, `15/11`, `amanhã`, `+7`"
            ))
        elif campo in ("responsavel", "revisor"):
            await _say(text=(
                f"{label} *novo para* _{sess['editar_titulo']}_\n"
                f"Escolha:\n{_USUARIOS_STR}"
            ))
        else:
            await _say(text=f"📝 *Nova descrição para* _{sess['editar_titulo']}_:")
        return True

    # Step 3: recebe o novo valor
    if step.startswith("editar_valor_"):
        campo   = sess.get("editar_campo", "")
        novo_valor = text

        if campo == "prazo":
            prazo = parse_prazo(text)
            if not prazo:
                await _say(text="⚠️ Formato inválido. Use `YYYY-MM-DD`, `DD/MM`, `amanhã` ou `+N`.")
                return True
            novo_valor = prazo

        elif campo in ("responsavel", "revisor"):
            nome = match_usuario(text)
            if not nome:
                await _say(text=f"⚠️ Não reconheci. Escolha:\n{_USUARIOS_STR}")
                return True
            novo_valor = nome

        # Aplica no Notion (se tiver ID)
        notion_id = sess.get("editar_notion_id")
        notion_ok = False
        if notion_id:
            notion_ok = await _aplicar_no_notion(notion_id, campo, novo_valor)

        # Invalida cache (conteúdo do Notion mudou)
        invalidar_cache()

        label = _CAMPOS_LABEL.get(campo, campo)
        notion_txt = "" if notion_ok else "\n⚠️ _Não foi possível atualizar no Notion — verifique manualmente._"

        await _say(text=(
            f"✅ *{label} atualizado!*\n"
            f"Card: _{sess['editar_titulo']}_\n"
            f"Novo valor: *{novo_valor}*"
            f"{notion_txt}\n\n"
            "Quer alterar outro campo? `1` prazo · `2` responsável · `3` revisor · `4` descrição · `cancelar`"
        ))
        sess["step"] = "editar_campo"
        return True

    return False


async def _aplicar_no_notion(page_id: str, campo: str, valor: str) -> bool:
    """Atualiza propriedade no Notion via API."""
    try:
        import os
        import httpx

        NOTION_TOKEN  = os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN", "")
        if not NOTION_TOKEN or not page_id:
            return False

        headers = {
            "Authorization":  f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type":   "application/json",
        }

        if campo == "prazo":
            props = {"Prazo": {"date": {"start": valor}}}
        elif campo == "responsavel":
            from card_constants import USUARIOS_UNICOS
            uid = USUARIOS_UNICOS.get(valor)
            if not uid:
                return False
            props = {"Responsável": {"people": [{"object": "user", "id": uid}]}}
        elif campo == "revisor":
            from card_constants import USUARIOS_UNICOS
            uid = USUARIOS_UNICOS.get(valor)
            if not uid:
                return False
            props = {"Revisor": {"people": [{"object": "user", "id": uid}]}}
        elif campo == "descricao":
            props = {"Descrição": {"rich_text": [{"text": {"content": valor}}]}}
        else:
            return False

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=headers,
                json={"properties": props},
            )
            return r.status_code == 200

    except Exception as e:
        logger.warning("[editar-card] Erro ao atualizar Notion: %s", e)
        return False
