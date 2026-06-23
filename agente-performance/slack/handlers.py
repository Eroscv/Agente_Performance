"""
slack/handlers.py

Handlers Slack Bolt para o Agente de Performance Guará.
Responde a @menções e DMs com análises geradas via Claude.

Fluxo:
  1. Usuário menciona o bot ou envia DM
  2. ACK imediato (evita timeout do Slack de 3s)
  3. engine.consultar() executa query no SQLite
  4. claude_client.gerar_resposta() formata com Claude
  5. Resposta postada no thread original
"""

import logging
import os
import re

from slack_bolt.async_app import AsyncApp
from dotenv import load_dotenv

from agent.engine import consultar
from agent.claude_client import gerar_resposta

load_dotenv()

logger = logging.getLogger(__name__)

app = AsyncApp(
    token=os.getenv("SLACK_BOT_TOKEN", ""),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET", ""),
)


def get_slack_app() -> AsyncApp:
    return app


def _limpar_mencao(text: str, bot_user_id: str | None) -> str:
    """Remove a menção ao bot do início do texto."""
    if bot_user_id:
        text = re.sub(rf"<@{bot_user_id}>", "", text, flags=re.IGNORECASE)
    return text.strip()


async def _processar_e_responder(
    text: str,
    channel: str,
    thread_ts: str,
    client,
):
    """Núcleo: consulta banco → gera resposta com Claude → posta no Slack."""
    try:
        # 1. Consulta ao banco via engine
        resultado = consultar(text)
        dados   = resultado["dados"]
        intent  = resultado["intent"]
        meta    = resultado["meta"]

        # 2. Gera resposta conversacional com Claude
        resposta = gerar_resposta(text, dados, intent, meta)

        # 3. Posta no thread
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

    text       = _limpar_mencao(event.get("text", ""), event.get("user"))
    channel    = event["channel"]
    thread_ts  = event.get("thread_ts") or event["ts"]

    if not text:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="Olá! 👋 Me pergunte algo sobre as campanhas. Ex: _qual campanha tem melhor ROAS?_",
        )
        return

    # Reação de "pensando"
    try:
        await client.reactions_add(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass

    await _processar_e_responder(text, channel, thread_ts, client)

    # Remove reação de "pensando"
    try:
        await client.reactions_remove(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass


# ── Evento: mensagem direta (DM) ─────────────────────────────────────────────

@app.event("message")
async def handle_dm(event, client, ack):
    await ack()

    # Só responde DMs (channel_type = im) e ignora mensagens do próprio bot
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id") or event.get("subtype"):
        return

    text      = event.get("text", "").strip()
    channel   = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]

    if not text:
        return

    # Reação de "pensando"
    try:
        await client.reactions_add(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass

    await _processar_e_responder(text, channel, thread_ts, client)

    # Remove reação de "pensando"
    try:
        await client.reactions_remove(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass
