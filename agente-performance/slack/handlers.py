"""
slack/handlers.py

Handlers Slack Bolt para o Agente de Performance Guará.
Responde a @menções e DMs com análises geradas via Claude.

Fluxo:
  1. Usuário menciona o bot ou envia DM
  2. ACK imediato (evita timeout do Slack de 3s)
  3. Contexto da thread carregado do context_store
  4. engine.consultar() com NLP + fuzzy + contexto
  5. claude_client.gerar_resposta() formata com Claude
  6. Resposta postada no thread + histórico salvo
"""

import logging
import os
import re

from slack_bolt.async_app import AsyncApp
from dotenv import load_dotenv

from agent.engine import consultar
from agent.claude_client import gerar_resposta
from agent.context_store import append_history, build_context_summary

load_dotenv()

logger = logging.getLogger(__name__)

app = AsyncApp(
    token=os.getenv("SLACK_BOT_TOKEN", ""),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET", ""),
)


def get_slack_app() -> AsyncApp:
    return app


def _limpar_mencao(text: str, bot_user_id: str | None) -> str:
    if bot_user_id:
        text = re.sub(rf"<@{bot_user_id}>", "", text, flags=re.IGNORECASE)
    return text.strip()


async def _processar_e_responder(
    text:      str,
    channel:   str,
    thread_ts: str,
    client,
):
    """Núcleo: NLP → query banco → Claude → posta no Slack → salva histórico."""
    try:
        # Salva pergunta no histórico da thread
        append_history(thread_ts, channel, "user", text)

        # Busca contexto da thread para perguntas de acompanhamento
        contexto = build_context_summary(thread_ts)

        # Consulta com NLP + contexto
        resultado   = consultar(text, thread_ts=thread_ts, channel=channel)
        dados       = resultado["dados"]
        intent      = resultado["intent"]
        meta        = resultado["meta"]
        dados_extra = resultado.get("dados_extra", {})

        # Gera resposta conversacional com Claude
        resposta = gerar_resposta(
            pergunta=text,
            dados=dados,
            intent=intent,
            meta=meta,
            dados_extra=dados_extra,
            contexto=contexto,
        )

        # Salva resposta no histórico
        append_history(thread_ts, channel, "agent", resposta[:500], meta)

        # Posta no thread
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=resposta,
            mrkdwn=True,
        )

    except Exception as e:
        logger.exception("Erro ao processar mensagem: %s", e)
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"⚠️ Ocorreu um erro ao processar sua consulta: `{e}`\nTente novamente.",
            mrkdwn=True,
        )


# ── Evento: menção ao bot (@agente) ──────────────────────────────────────────

@app.event("app_mention")
async def handle_mention(event, say, client, ack):
    await ack()

    text      = _limpar_mencao(event.get("text", ""), event.get("user"))
    channel   = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]

    if not text:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="Olá! 👋 Me pergunte algo sobre as campanhas.\nEx: _qual campanha tem melhor ROAS essa semana?_",
        )
        return

    try:
        await client.reactions_add(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass

    await _processar_e_responder(text, channel, thread_ts, client)

    try:
        await client.reactions_remove(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass


# ── Evento: mensagem direta (DM) ─────────────────────────────────────────────

@app.event("message")
async def handle_dm(event, client, ack):
    await ack()

    if event.get("channel_type") != "im":
        return
    if event.get("bot_id") or event.get("subtype"):
        return

    text      = event.get("text", "").strip()
    channel   = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]

    if not text:
        return

    try:
        await client.reactions_add(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass

    await _processar_e_responder(text, channel, thread_ts, client)

    try:
        await client.reactions_remove(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass
