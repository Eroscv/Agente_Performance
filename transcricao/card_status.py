"""
card_status.py
Comando /status-cards — consulta cards do Notion por responsável.
- Cache SQLite de 5 min (evita chamadas repetidas ao Notion)
- Filtro por status: /status-cards Bianca em andamento
"""
import logging
from card_constants import USUARIOS_UNICOS
from card_helpers import match_usuario
from card_notion import buscar_cards, formatar_cards
from card_db import get_cache, set_cache

logger = logging.getLogger(__name__)

# Status válidos para filtro
_STATUS_VALIDOS = {
    "a fazer":      "A fazer",
    "fazer":        "A fazer",
    "andamento":    "Em andamento",
    "em andamento": "Em andamento",
    "revisão":      "Em revisão",
    "revisao":      "Em revisão",
    "em revisão":   "Em revisão",
    "concluído":    "Concluído",
    "concluido":    "Concluído",
    "bloqueado":    "Bloqueado",
}


def _parse_arg(arg: str) -> tuple[str, str | None]:
    """
    Separa nome e filtro de status do argumento.
    Ex: 'Bianca em andamento' → ('Bianca', 'Em andamento')
    Ex: 'todos bloqueado'     → ('todos', 'Bloqueado')
    Ex: 'Bianca'              → ('Bianca', None)
    """
    arg_lower = arg.lower()
    for kw, status in _STATUS_VALIDOS.items():
        if arg_lower.endswith(kw):
            nome = arg[:-(len(kw))].strip()
            return nome or arg, status
    return arg, None


async def cmd_status_cards(ack, command, say):
    """Slash /status-cards [nome] [status] — lista cards do Notion."""
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
            "🗂️ *Consulta de cards* — mostra cards *não concluídos* por responsável.\n\n"
            "*Responsáveis disponíveis:*\n"
            + "\n".join(f"  {i+1}. {n}" for i, n in enumerate(USUARIOS_UNICOS.keys()))
            + "\n\n*Uso:*\n"
            "• `/status-cards Bianca` — todos os cards abertos da Bianca\n"
            "• `/status-cards Bianca em andamento` — só os em andamento\n"
            "• `/status-cards todos` — todos os cards em aberto\n"
            "• `/status-cards todos bloqueado` — só os bloqueados\n\n"
            "*Status disponíveis:* `a fazer` · `em andamento` · `em revisão` · `bloqueado` · `concluído`"
        ))
        return

    nome_arg, filtro_status = _parse_arg(arg)

    if nome_arg.lower() in ("todos", "all", "tudo"):
        nome_exibido = "todos os responsáveis"
        cache_key    = f"all:{filtro_status or 'abertos'}"
        cards = get_cache(cache_key)
        if cards is None:
            cards = buscar_cards(responsavel_id=None, limit=30)
            set_cache(cache_key, cards)
        mostrar_resp = True
    else:
        nome = match_usuario(nome_arg)
        if not nome:
            await _say(text=(
                f"⚠️ Responsável *{nome_arg}* não encontrado. Nomes disponíveis:\n"
                + "\n".join(f"  {i+1}. {n}" for i, n in enumerate(USUARIOS_UNICOS.keys()))
            ))
            return
        nome_exibido = nome
        uid       = USUARIOS_UNICOS[nome]
        cache_key = f"uid:{uid}:{filtro_status or 'abertos'}"
        cards = get_cache(cache_key)
        if cards is None:
            cards = buscar_cards(responsavel_id=uid, limit=20)
            set_cache(cache_key, cards)
        mostrar_resp = False

    # Aplica filtro de status se informado
    if filtro_status:
        cards = [c for c in cards if c.get("status", "").lower() == filtro_status.lower()]

    if not cards:
        filtro_txt = f" com status *{filtro_status}*" if filtro_status else ""
        await _say(text=f"✅ Nenhum card encontrado para *{nome_exibido}*{filtro_txt}.")
        return

    atrasados  = sum(1 for c in cards if "Atrasad" in c.get("situacao", ""))
    filtro_txt = f" — filtro: _{filtro_status}_" if filtro_status else ""
    header = (
        f"📋 *Cards — {nome_exibido}*{filtro_txt}\n"
        f"Total: *{len(cards)}* card(s)"
        + (f" │ ⚠️ *{atrasados} atrasado(s)*" if atrasados else "")
        + "\n" + "─" * 30
    )
    corpo = formatar_cards(cards, mostrar_responsavel=mostrar_resp)
    await _say(text=f"{header}\n\n{corpo}")
