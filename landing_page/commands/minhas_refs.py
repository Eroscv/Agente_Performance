"""
landing_page/commands/minhas_refs.py
/minhas-refs — listar e descartar referências visuais acumuladas.
"""
import logging
from ..db.queries import get_refs, desativar_ref
from ..constants import get_session, _sessions

logger = logging.getLogger(__name__)


async def cmd_minhas_refs(ack, command, say):
    await ack()
    user_id   = command["user_id"]
    channel   = command["channel_id"]
    thread_ts = command.get("thread_ts")

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        await say(**kwargs)

    boas  = get_refs(user_id, "boa",  limite=15)
    ruins = get_refs(user_id, "ruim", limite=15)

    if not boas and not ruins:
        await _say(text=(
            "📭 Você ainda não tem referências visuais salvas.\n"
            "Elas são coletadas automaticamente ao usar `/criar-landing`."
        ))
        return

    linhas = ["📌 *Suas referências visuais acumuladas:*\n" + "─" * 34]

    if boas:
        linhas.append("✅ *Positivas (o que você gosta):*")
        for r in boas:
            linhas.append(f"  `#{r['id']}` {r['descricao']} _{r['criado_em'][:10]}_")

    if ruins:
        linhas.append("\n🚫 *A evitar (o que você não quer):*")
        for r in ruins:
            linhas.append(f"  `#{r['id']}` {r['descricao']} _{r['criado_em'][:10]}_")

    linhas.append(
        "\n_Para descartar uma ref: responda com o número. Ex: `descartar 3` ou `descartar 3 7 12`_"
    )

    # Inicia sessão de gerenciamento
    get_session(user_id).update({
        "step":    "refs_gerenciar",
        "channel": channel,
        "thread_ts": thread_ts,
    })

    await _say(text="\n".join(linhas))


async def handle_message_minhas_refs(user_id: str, text: str, say,
                                      channel: str, thread_ts: str | None = None) -> bool:
    """Processa 'descartar N [N...]' na sessão de gerenciamento de refs."""
    sess = _sessions.get(user_id)
    if not sess or sess.get("step") != "refs_gerenciar":
        return False

    text_l = text.lower().strip()
    effective_ts = thread_ts or sess.get("thread_ts")

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if effective_ts:
            kwargs.setdefault("thread_ts", effective_ts)
        await say(**kwargs)

    if text_l in ("sair", "ok", "fechar", "cancelar"):
        from ..constants import clear_session
        clear_session(user_id)
        await _say(text="✅ Gerenciamento de refs encerrado.")
        return True

    if text_l.startswith("descartar"):
        import re
        ids = re.findall(r"\d+", text_l)
        if not ids:
            await _say(text="⚠️ Informe o número da ref. Ex: `descartar 3` ou `descartar 3 7`")
            return True

        removidas = []
        nao_encontradas = []
        for ref_id in ids:
            if desativar_ref(int(ref_id), user_id):
                removidas.append(ref_id)
            else:
                nao_encontradas.append(ref_id)

        msg = ""
        if removidas:
            msg += f"🗑️ Refs removidas: {', '.join('#' + i for i in removidas)}\n"
        if nao_encontradas:
            msg += f"⚠️ Não encontradas: {', '.join('#' + i for i in nao_encontradas)}\n"
        msg += "_Continue descartando ou diga `sair`._"
        await _say(text=msg)
        return True

    return False
