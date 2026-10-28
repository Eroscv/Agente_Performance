"""
landing_page/commands/variar_lp.py
/variar-lp [id] — gera nova versão de uma LP mudando só um campo do briefing.
"""
import logging
from ..db.queries import buscar_projeto, listar_projetos
from ..constants import get_session, clear_session, _sessions
from ..generator import gerar_lp
from ..db.queries import abrir_sessao

logger = logging.getLogger(__name__)

_CAMPOS = {
    "1": "objetivo",   "objetivo":  "objetivo",
    "2": "tom",        "tom":       "tom",
    "3": "publico",   "publico":  "publico",
    "4": "extras",     "extras":    "extras",
    "5": "refs_boas",  "refs":      "refs_boas",
    "6": "nada",       "nada":      "nada",
}

_CAMPOS_LABEL = {
    "objetivo": "🎯 Objetivo",
    "tom":      "🗣️ Tom de voz",
    "publico":  "👥 Público-alvo",
    "extras":   "📝 Informações extras",
    "refs_boas":"✅ Referências positivas",
    "nada":     "🔄 Gerar novamente (sem mudanças)",
}


async def cmd_variar_lp(ack, command, say):
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

    # Busca projeto
    projeto = None
    if arg.isdigit():
        projeto = buscar_projeto(int(arg))
        if projeto and projeto["user_id"] != user_id:
            await _say(text="⚠️ Projeto não encontrado ou não pertence a você.")
            return

    if not projeto:
        # Mostra recentes para escolher
        recentes = listar_projetos(user_id=user_id, limite=6)
        if not recentes:
            await _say(text="📭 Você ainda não tem projetos. Use `/criar-landing`.")
            return
        opcoes = "\n".join(
            f"  *{p['id']}.* {p['cliente']} — _{p['objetivo']}_ ({p['criado_em'][:10]})"
            for p in recentes
        )
        get_session(user_id).update({
            "step": "variar_escolher", "channel": channel, "thread_ts": thread_ts,
        })
        await _say(text=f"🔄 *Variar LP — escolha um projeto:*\n\n{opcoes}\n\nResponda com o número.")
        return

    _iniciar_variacao(user_id, projeto, channel, thread_ts)
    await _say(text=_menu_variacao(projeto))


def _iniciar_variacao(user_id: str, projeto: dict, channel: str, thread_ts) -> None:
    get_session(user_id).update({
        "step":           "variar_campo",
        "variar_projeto": projeto,
        "channel":        channel,
        "thread_ts":      thread_ts,
    })


def _menu_variacao(projeto: dict) -> str:
    return (
        f"🔄 *Variando:* _{projeto['cliente']} — {projeto['objetivo']}_\n\n"
        f"Qual campo quer alterar?\n"
        f"  1️⃣ Objetivo (atual: _{projeto.get('objetivo', '—')}_)\n"
        f"  2️⃣ Tom de voz (atual: _{projeto.get('tom', '—')}_)\n"
        f"  3️⃣ Público-alvo (atual: _{projeto.get('publico', '—')}_)\n"
        f"  4️⃣ Extras / Promoção\n"
        f"  5️⃣ Referências visuais\n"
        f"  6️⃣ Gerar novamente sem mudanças\n\n"
        "`cancelar` para sair."
    )


async def handle_message_variar_lp(user_id: str, text: str, say,
                                    channel: str, thread_ts: str | None = None) -> bool:
    sess = _sessions.get(user_id)
    if not sess or not sess.get("step", "").startswith("variar"):
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
        await _say(text="🚫 Variação cancelada.")
        return True

    if step == "variar_escolher":
        if not text.isdigit():
            await _say(text="⚠️ Responda com o número do projeto.")
            return True
        projeto = buscar_projeto(int(text))
        if not projeto or projeto["user_id"] != user_id:
            await _say(text="⚠️ Projeto não encontrado.")
            return True
        _iniciar_variacao(user_id, projeto, channel, effective_ts)
        await _say(text=_menu_variacao(projeto))
        return True

    if step == "variar_campo":
        campo = _CAMPOS.get(text.lower())
        if not campo:
            await _say(text="⚠️ Escolha 1–6 ou `cancelar`.")
            return True

        if campo == "nada":
            # Gera direto sem mudança
            await _disparar_geracao(user_id, sess, _say, channel, effective_ts)
            return True

        sess["step"]        = "variar_valor"
        sess["variar_campo"] = campo
        label = _CAMPOS_LABEL[campo]
        await _say(text=f"*{label}* — qual o novo valor?")
        return True

    if step == "variar_valor":
        campo   = sess.get("variar_campo")
        projeto = sess.get("variar_projeto", {})
        novo_valor = text

        # Monta sessão de geração com briefing original + campo alterado
        sess["step"] = "variar_gerando"
        await _disparar_geracao(user_id, sess, _say, channel, effective_ts,
                                 campo_alterado=campo, novo_valor=novo_valor)
        return True

    return False


async def _disparar_geracao(user_id: str, sess: dict, say, channel: str,
                              thread_ts, campo_alterado: str = None, novo_valor: str = None):
    projeto = sess.get("variar_projeto", {})
    sid = abrir_sessao(user_id)

    # Monta sessão de geração baseada no projeto original
    sess_gen = {
        "sessao_id":  sid,
        "cliente":    projeto.get("cliente", ""),
        "objetivo":   projeto.get("objetivo", ""),
        "publico":    projeto.get("publico", ""),
        "tom":        projeto.get("tom", ""),
        "extras":     projeto.get("extras", ""),
        "refs_boas":  projeto.get("refs_boas", ""),
        "refs_ruins": projeto.get("refs_ruins", ""),
        "channel":    channel,
        "thread_ts":  thread_ts,
    }

    # Aplica alteração
    if campo_alterado and novo_valor:
        sess_gen[campo_alterado] = novo_valor
        campo_label = _CAMPOS_LABEL.get(campo_alterado, campo_alterado)
        await say(
            channel=channel,
            thread_ts=thread_ts,
            text=f"🔄 Gerando variação com *{campo_label}* alterado para: _{novo_valor}_",
        )
    else:
        await say(
            channel=channel,
            thread_ts=thread_ts,
            text=f"🔄 Gerando nova versão de _{projeto.get('cliente', '')}_...",
        )

    clear_session(user_id)
    await gerar_lp(user_id, sess_gen, say, channel, thread_ts)
