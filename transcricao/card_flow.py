"""
card_flow.py
Fluxo conversacional do /criar-card — steps: cliente_card → frente → entrega
→ responsavel → revisor → prazo → descricao → confirmar.
"""
import logging
from datetime import timedelta, datetime
from card_constants import (
    get_session, clear_session,
    USUARIOS_UNICOS, CLIENTES_LISTA, FRENTES_LISTA,
    _USUARIOS_STR, _FRENTES_STR, _sessions,
)
from card_helpers import parse_prazo, match_usuario, match_cliente, match_frente
from card_notion import criar_no_notion
from card_db import abrir_sessao, atualizar_step, encerrar_sessao as _enc_sessao, sessoes_timeout

logger = logging.getLogger(__name__)


async def cmd_criar_card(ack, command, say):
    await ack()
    user_id   = command["user_id"]
    channel   = command["channel_id"]
    thread_ts = command.get("thread_ts")
    clear_session(user_id)
    abrir_sessao(user_id)
    get_session(user_id).update({"step": "cliente_card", "channel": channel, "thread_ts": thread_ts})

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        await say(**kwargs)

    clientes_num = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(CLIENTES_LISTA))
    await _say(text=(
        "🗂️ *Criar card no Notion* — formato: `Cliente: Frente - Entrega`\n\n"
        "1️⃣ *Para qual cliente é a tarefa?*\n"
        f"{clientes_num}\n\n"
        "Digite o número ou parte do nome."
    ))


async def handle_message_criar_card(user_id: str, text: str, say, channel: str,
                                     thread_ts: str | None = None) -> bool:
    """Retorna True se a mensagem foi consumida pelo fluxo de criação de card."""
    # Limpa sessões com timeout (>60 min sem resposta)
    for uid_timeout in sessoes_timeout(minutos=60):
        _enc_sessao(uid_timeout, "timeout")
        clear_session(uid_timeout)
        if uid_timeout == user_id:
            # Avisa o usuário que a sessão expirou
            effective_ts_t = thread_ts
            async def _say_timeout(**kwargs):
                kwargs.setdefault("channel", channel)
                if effective_ts_t:
                    kwargs.setdefault("thread_ts", effective_ts_t)
                await say(**kwargs)
            await _say_timeout(text=(
                "⏱️ Sua sessão de criação de card expirou (60 min sem resposta).\n"
                "Digite `/criar-card` para começar novamente."
            ))
            return True

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
        _enc_sessao(user_id, "cancelado")
        clear_session(user_id)
        await _say(text="🚫 Criação de card cancelada.")
        return True

    if step == "cliente_card":
        cliente_card = match_cliente(text)
        if not cliente_card:
            clientes_num = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(CLIENTES_LISTA))
            await _say(text=f"⚠️ Não reconheci. Escolha:\n{clientes_num}")
            return True
        sess["cliente_card"] = cliente_card
        sess["step"]         = "frente"
        atualizar_step(user_id, "frente")
        await _say(text=(
            f"✅ Cliente: *{cliente_card}*\n\n"
            f"2️⃣ *Qual é a frente de trabalho?*\n{_FRENTES_STR}\n\n"
            "Digite o número ou parte do nome."
        ))
        return True

    if step == "frente":
        frente = match_frente(text)
        if not frente:
            await _say(text=f"⚠️ Frente não reconhecida. Opções:\n{_FRENTES_STR}")
            return True
        sess["frente"] = frente
        sess["step"]   = "entrega"
        atualizar_step(user_id, "entrega")
        await _say(text=(
            f"✅ Frente: *{frente}*\n\n"
            f"3️⃣ *Qual é a entrega?*\n"
            f"Descreva o que precisa ser feito (verbo no infinitivo, até 6 palavras).\n"
            f"Ex: `Variação campeão Dimples` / `Relatório mensal` / `Postagens da semana`\n\n"
            f"📌 Título que será criado: *{sess['cliente_card']}: {frente} - [sua entrega]*"
        ))
        return True

    if step == "entrega":
        if not text:
            await _say(text="⚠️ Informe a entrega. Tente novamente.")
            return True
        titulo = f"{sess.get('cliente_card', '')}: {sess.get('frente', '')} - {text}"
        sess["titulo"] = titulo
        sess["step"]   = "responsavel"
        atualizar_step(user_id, "responsavel")
        await _say(text=(
            f"✅ Título: *{titulo}*\n\n"
            f"4️⃣ *Quem é o responsável?*\n{_USUARIOS_STR}\n\n"
            "Digite o número ou parte do nome."
        ))
        return True

    if step == "responsavel":
        nome = match_usuario(text)
        if not nome:
            await _say(text=f"⚠️ Não reconheci. Escolha:\n{_USUARIOS_STR}")
            return True
        sess["responsavel"] = nome
        sess["step"]        = "revisor"
        atualizar_step(user_id, "revisor")
        await _say(text=(
            f"✅ Responsável: *{nome}*\n\n"
            f"5️⃣ *Quem revisa?*\n{_USUARIOS_STR}\n\n"
            "Digite o número ou parte do nome."
        ))
        return True

    if step == "revisor":
        nome = match_usuario(text)
        if not nome:
            await _say(text=f"⚠️ Não reconheci. Escolha:\n{_USUARIOS_STR}")
            return True
        sess["revisor"] = nome
        sess["step"]    = "prazo"
        atualizar_step(user_id, "prazo")
        await _say(text=(
            f"✅ Revisor: *{nome}*\n\n"
            "6️⃣ *Qual é o prazo?*\n"
            "Ex: `2026-11-10`, `10/11/2026`, `amanhã`, `+7` (em 7 dias)\n"
            "Ou `pular` para usar +7 dias a partir de hoje."
        ))
        return True

    if step == "prazo":
        if text.lower() in ("pular", "skip", "-"):
            prazo = (datetime.now().date() + timedelta(days=7)).isoformat()
        else:
            prazo = parse_prazo(text)
            if not prazo:
                await _say(text="⚠️ Formato inválido. Use `YYYY-MM-DD`, `DD/MM/YYYY`, `DD/MM`, `amanhã`, `hoje` ou `+N`.")
                return True
        sess["prazo"] = prazo
        sess["step"]  = "descricao"
        atualizar_step(user_id, "descricao")
        await _say(text=(
            f"✅ Prazo: *{prazo}*\n\n"
            "7️⃣ *Descrição da tarefa* (opcional)\n"
            "Detalhe o que precisa ser feito, ou `pular`."
        ))
        return True

    if step == "descricao":
        sess["descricao"] = "" if text.lower() in ("pular", "skip", "-") else text
        sess["step"]      = "confirmar"
        sess.setdefault("cliente", sess.get("cliente_card", ""))
        titulo      = sess.get("titulo", "")
        responsavel = sess.get("responsavel", "")
        revisor     = sess.get("revisor", "")
        prazo       = sess.get("prazo", "")
        cliente     = sess.get("cliente", "")
        frente      = sess.get("frente", "")
        descricao   = sess.get("descricao", "") or "_sem descrição_"
        await _say(text=(
            "📋 *Resumo do card — revise:*\n\n"
            f"• *Título:* {titulo}\n"
            f"• *Cliente:* {cliente}\n"
            f"• *Frente:* {frente}\n"
            f"• *Responsável:* {responsavel}\n"
            f"• *Revisor:* {revisor}\n"
            f"• *Prazo:* {prazo}\n"
            f"• *Descrição:* {descricao}\n\n"
            "Digite *confirmar* para criar o card ou *cancelar* para abortar."
        ))
        return True

    if step == "confirmar":
        if text.lower() == "confirmar":
            await criar_no_notion(user_id, sess, _say)
            return True
        else:
            await _say(text="Digite *confirmar* para criar ou *cancelar* para abortar.")
            return True

    return False
