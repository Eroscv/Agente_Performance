"""
card_status.py
Comando /status-cards — consulta cards do Notion por responsável.
"""
import logging
from card_constants import USUARIOS_UNICOS, _sessions
from card_helpers import match_usuario
from card_notion import buscar_cards, formatar_cards

logger = logging.getLogger(__name__)


async def cmd_status_cards(ack, command, say):
    """Slash /status-cards [nome] — lista cards abertos do Notion."""
    await ack()
    channel   = command["channel_id"]
    thread_ts = command.get("thread_ts")
    arg       = command.get("text", "").strip()

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        await say(**kwargs)

    if not arg:
        await _say(text=(
            "🗂️ *Consulta de cards* — mostra cards *não concluídos*, filtrado por responsável.\n\n"
            "*Responsáveis disponíveis:*\n"
            + "\n".join(f"  {i+1}. {n}" for i, n in enumerate(USUARIOS_UNICOS.keys()))
            + "\n\nUso:\n"
            "• `/status-cards Bianca` — cards da Bianca\n"
            "• `/status-cards todos` — todos os cards em aberto"
        ))
        return

    if arg.lower() in ("todos", "all", "tudo"):
        nome_exibido = "todos os responsáveis"
        cards = buscar_cards(responsavel_id=None, limit=25)
        mostrar_resp = True
    else:
        nome = match_usuario(arg)
        if not nome:
            await _say(text=(
                f"⚠️ Responsável *{arg}* não encontrado. Nomes disponíveis:\n"
                + "\n".join(f"  {i+1}. {n}" for i, n in enumerate(USUARIOS_UNICOS.keys()))
            ))
            return
        nome_exibido = nome
        uid   = USUARIOS_UNICOS[nome]
        cards = buscar_cards(responsavel_id=uid, limit=20)
        mostrar_resp = False

    if not cards:
        await _say(text=f"✅ Nenhum card em aberto para *{nome_exibido}*.")
        return

    atrasados = sum(1 for c in cards if "Atrasad" in c.get("situacao", ""))
    header = (
        f"📋 *Cards em aberto — {nome_exibido}*\n"
        f"Total: *{len(cards)}* card(s)"
        + (f" │ ⚠️ *{atrasados} atrasado(s)*" if atrasados else "")
        + "\n" + "─" * 30
    )
    corpo = formatar_cards(cards, mostrar_responsavel=mostrar_resp)
    await _say(text=f"{header}\n\n{corpo}")
