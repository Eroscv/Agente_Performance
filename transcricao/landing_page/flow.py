"""
landing_page/flow.py
Fluxo conversacional do /criar-landing — 7 steps, 100% texto.

Steps:
  cliente → objetivo → publico → tom → extras
  → refs_boas → refs_ruins → confirmar → [gera]
"""
import logging
from .constants import get_session, clear_session
from .generator import gerar_lp

logger = logging.getLogger(__name__)


async def cmd_criar_landing(ack, command, say):
    await ack()
    user_id   = command["user_id"]
    channel   = command["channel_id"]
    thread_ts = command.get("thread_ts")
    clear_session(user_id)
    get_session(user_id).update({"step": "cliente", "channel": channel, "thread_ts": thread_ts})

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        await say(**kwargs)

    await _say(text=(
        "🚀 *Criar Landing Page* — vou fazer 7 perguntas para montar o briefing.\n\n"
        "1️⃣ *Para qual cliente e produto é a landing page?*\n"
        "Ex: `Source OF — Colágeno Verisol 300g`\n"
        "Ex: `POD Kombucha — Kit Iniciante 6 sabores`"
    ))


async def handle_message_landing(user_id: str, text: str, say, channel: str,
                                  thread_ts: str | None = None) -> bool:
    """Retorna True se a mensagem foi consumida pelo fluxo de landing page."""
    from .constants import _sessions
    sess = _sessions.get(user_id)
    if not sess:
        return False

    step = sess.get("step")
    text = text.strip()
    effective_ts = thread_ts or sess.get("thread_ts")

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if effective_ts:
            kwargs.setdefault("thread_ts", effective_ts)
        await say(**kwargs)

    if text.lower() in ("cancelar", "cancel", "sair"):
        clear_session(user_id)
        await _say(text="🚫 Criação de landing page cancelada.")
        return True

    if step == "cliente":
        if not text:
            await _say(text="⚠️ Informe o cliente/produto. Tente novamente.")
            return True
        sess["cliente"] = text
        sess["step"]    = "objetivo"
        await _say(text=(
            f"✅ Cliente/Produto: *{text}*\n\n"
            "2️⃣ *Qual é o objetivo principal desta landing page?*\n"
            "Opções: `captação de leads`, `venda direta`, `lançamento de produto`, `evento/webinar`, `teste A/B`\n"
            "Ou descreva livremente."
        ))
        return True

    if step == "objetivo":
        if not text:
            await _say(text="⚠️ Informe o objetivo. Tente novamente.")
            return True
        sess["objetivo"] = text
        sess["step"]     = "publico"
        await _say(text=(
            f"✅ Objetivo: *{text}*\n\n"
            "3️⃣ *Quem é o público-alvo?*\n"
            "Ex: `Mulheres 30–50 anos, interessadas em saúde e bem-estar`"
        ))
        return True

    if step == "publico":
        if not text:
            await _say(text="⚠️ Informe o público-alvo. Tente novamente.")
            return True
        sess["publico"] = text
        sess["step"]    = "tom"
        await _say(text=(
            f"✅ Público: *{text}*\n\n"
            "4️⃣ *Qual é o tom de voz?*\n"
            "Opções: `urgente e direto`, `empático e educativo`, `aspiracional/premium`, `descontraído e jovem`, `técnico/especialista`\n"
            "Ou descreva livremente."
        ))
        return True

    if step == "tom":
        if not text:
            await _say(text="⚠️ Informe o tom de voz. Tente novamente.")
            return True
        sess["tom"]  = text
        sess["step"] = "extras"
        await _say(text=(
            f"✅ Tom: *{text}*\n\n"
            "5️⃣ *Informações extras* (opcional)\n"
            "Inclua: promoção, preço, benefícios, depoimentos, garantia...\n"
            "Ou `pular`."
        ))
        return True

    if step == "extras":
        sess["extras"] = "" if text.lower() in ("pular", "skip", "-") else text
        sess["step"]   = "refs_boas"
        await _say(text=(
            "6️⃣ *Referências visuais — o que você GOSTA* (opcional)\n"
            "Sites, marcas ou estilos que você considera referência positiva.\n"
            "Ex: `Apple.com — clean, espaço em branco, tipografia grande`\n"
            "Ou `pular`."
        ))
        return True

    if step == "refs_boas":
        sess["refs_boas"] = "" if text.lower() in ("pular", "skip", "-") else text
        sess["step"]      = "refs_ruins"
        await _say(text=(
            "7️⃣ *Referências visuais — o que você NÃO QUER* (opcional)\n"
            "Estilos ou elementos a evitar no site.\n"
            "Ex: `Fundo escuro, countdown fake, visual gritante de info-produto`\n"
            "Ou `pular`."
        ))
        return True

    if step == "refs_ruins":
        sess["refs_ruins"] = "" if text.lower() in ("pular", "skip", "-") else text
        sess["step"]       = "confirmar"
        cliente    = sess.get("cliente", "")
        objetivo   = sess.get("objetivo", "")
        publico    = sess.get("publico", "")
        tom        = sess.get("tom", "")
        extras     = sess.get("extras", "") or "agente usará melhores práticas"
        refs_boas  = sess.get("refs_boas", "") or "não informado"
        refs_ruins = sess.get("refs_ruins", "") or "não informado"
        await _say(text=(
            "📋 *Briefing completo — revise antes de gerar:*\n\n"
            f"• *Cliente/Produto:* {cliente}\n"
            f"• *Objetivo:* {objetivo}\n"
            f"• *Público-alvo:* {publico}\n"
            f"• *Tom de voz:* {tom}\n"
            f"• *Extras:* {extras}\n"
            f"• *Referências positivas:* {refs_boas}\n"
            f"• *A evitar:* {refs_ruins}\n\n"
            "Digite *confirmar* para gerar ou *cancelar* para abortar."
        ))
        return True

    if step == "confirmar":
        if text.lower() == "confirmar":
            sess["step"] = "gerando"
            await gerar_lp(user_id, sess, _say, channel, effective_ts)
            return True
        elif text.lower() in ("cancelar", "cancel"):
            clear_session(user_id)
            await _say(text="🚫 Criação de landing page cancelada.")
            return True
        else:
            await _say(text="Digite *confirmar* para gerar ou *cancelar* para abortar.")
            return True

    return False
