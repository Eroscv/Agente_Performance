"""
card_meus.py
Comando /meus-cards — histórico de cards criados pelo próprio usuário via Slack.
"""
import logging
from card_db import listar_cards

logger = logging.getLogger(__name__)


async def cmd_meus_cards(ack, command, say):
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

    # Limite opcional: /meus-cards 20
    try:
        limite = int(arg) if arg.isdigit() else 10
        limite = min(limite, 30)
    except ValueError:
        limite = 10

    cards = listar_cards(user_id=user_id, limite=limite)

    if not cards:
        await _say(text=(
            "📭 Você ainda não criou nenhum card via Slack.\n"
            "Use `/criar-card` para começar."
        ))
        return

    linhas = [f"📋 *Seus últimos {len(cards)} cards criados via Slack:*\n" + "─" * 30]
    for c in cards:
        status_icon = "✅" if c["status"] == "criado" else "❌"
        prazo_txt   = f" │ 📅 {c['prazo']}" if c.get("prazo") else ""
        url_txt     = f"\n   🔗 {c['url_notion']}" if c.get("url_notion") else ""
        data        = c["criado_em"][:16].replace("T", " ")
        linhas.append(
            f"{status_icon} *{c['titulo']}*\n"
            f"   👤 {c.get('responsavel', '—')} │ 🏢 {c.get('cliente', '—')}{prazo_txt}\n"
            f"   🕐 {data}{url_txt}"
        )

    await _say(text="\n\n".join(linhas))
