"""
landing_page/commands/meus_projetos.py
/meus-projetos — lista LPs geradas com briefing reutilizável.
"""
import logging
from ..db.queries import listar_projetos, get_html

logger = logging.getLogger(__name__)


async def cmd_meus_projetos(ack, command, say):
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

    try:
        limite = min(int(arg), 20) if arg.isdigit() else 8
    except ValueError:
        limite = 8

    projetos = listar_projetos(user_id=user_id, limite=limite)
    if not projetos:
        await _say(text=(
            "📭 Você ainda não gerou nenhuma landing page.\n"
            "Use `/criar-landing` para começar."
        ))
        return

    linhas = [f"🚀 *Seus últimos {len(projetos)} projetos de landing page:*\n" + "─" * 34]
    for p in projetos:
        status_icon = {"gerado": "✅", "falha": "❌", "gerando": "⏳", "cancelado": "🚫"}.get(p["status"], "❓")
        data = p["criado_em"][:16].replace("T", " ")
        html_txt = " │ 💾 _HTML salvo_" if get_html(p["id"]) else ""
        linhas.append(
            f"{status_icon} *#{p['id']} — {p['cliente']}*\n"
            f"   🎯 {p['objetivo']} │ 👥 {p['publico']}\n"
            f"   🗣️ _{p['tom']}_ │ 🕐 {data}{html_txt}\n"
            f"   Para reusar: `/variar-lp {p['id']}`"
        )

    linhas.append("\n_Use `/variar-lp [id]` para gerar uma nova versão mudando só um campo._")
    await _say(text="\n\n".join(linhas))
