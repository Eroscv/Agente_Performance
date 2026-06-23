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
from slack.formatter import markdown_to_blocks, truncar_blocos

AJUDA_TEXTO = """*⚙️ Agente de Performance Guará — Como consultar*

Me pergunte em linguagem natural, diretamente no chat ou me mencionando com @. Você pode incluir o nome da campanha, uma métrica e/ou um período.

*📅 Períodos que entendo*
> `essa semana` · `semana passada` · `esse mês` · `mês passado`
> `últimos 7 dias` · `últimos 30 dias` · `últimos 90 dias`
> `em maio` · `em novembro` · `em 2025` · `black friday`
> `ontem` · `hoje` · `segundo trimestre` · `janeiro a março`
> Datas diretas: `01/05/2026` ou `2026-05-01`

*💬 Exemplos de perguntas*

*1. Ranking de eficiência*
> _qual campanha teve melhor ROAS nos últimos 30 dias?_
> _top 5 campanhas mais eficientes esse mês_

*2. Performance de uma campanha*
> _como foi a ABO em maio?_
> _tá valendo a pena a campanha do hidratante?_
> _mostre o desempenho da Advantage de junho_

*3. Vendas e receita*
> _qual campanha gerou mais vendas na black friday?_
> _quanto faturamos com a ABO nos últimos 7 dias?_
> _top 3 campanhas por compras esse mês_

*4. Funil de conversão*
> _mostre o funil da campanha do hidratante_
> _qual a taxa de conversão geral em maio?_
> _quantos carrinhos abandonados na ABO essa semana?_

*5. Comparação e histórico*
> _compare o ROAS de novembro vs maio_
> _como evoluiu o gasto da ABO dia a dia em junho?_
> _resuma tudo que aconteceu no segundo trimestre_

*💡 Dica:* você pode combinar campanha + métrica + período na mesma frase:
> _qual o gasto e retorno da ABO essa semana?_

Qualquer dúvida é só perguntar! 🚀"""

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
    user_id:   str = None,
    formato_export: str = None,  # "excel" | "pdf" | None
):
    """Núcleo: NLP → query banco → Claude → posta no Slack → salva histórico."""
    try:
        append_history(thread_ts, channel, "user", text)
        contexto = build_context_summary(thread_ts)

        resultado   = consultar(text, thread_ts=thread_ts, channel=channel, user_id=user_id)
        dados       = resultado["dados"]
        intent      = resultado["intent"]
        meta        = resultado["meta"]
        dados_extra = resultado.get("dados_extra", {})
        erro        = resultado.get("erro")
        aviso_vazio = resultado.get("aviso_vazio")

        # Campanha não encontrada — responde diretamente sem chamar Claude
        if erro:
            await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=erro, mrkdwn=True,
            )
            return

        # Dados vazios com sugestão de período
        if aviso_vazio:
            await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=aviso_vazio, mrkdwn=True,
            )
            return

        # Exportação de arquivo
        if formato_export in ("excel", "pdf"):
            from reports.exporter import gerar_relatorio
            conteudo, filename, mimetype = gerar_relatorio(resultado, text, formato_export)
            await client.files_upload_v2(
                channel=channel,
                thread_ts=thread_ts,
                content=conteudo,
                filename=filename,
                title=f"Relatório — {intent}",
            )
            await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=f"✅ Arquivo *{filename}* gerado com sucesso!",
                mrkdwn=True,
            )
            return

        # Gera resposta conversacional com Claude
        resposta = gerar_resposta(
            pergunta=text, dados=dados, intent=intent, meta=meta,
            dados_extra=dados_extra, contexto=contexto,
        )

        append_history(thread_ts, channel, "agent", resposta[:500], meta)

        blocks = truncar_blocos(markdown_to_blocks(resposta))
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=resposta[:150], blocks=blocks,
        )

    except Exception as e:
        logger.exception("Erro ao processar mensagem: %s", e)
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f"⚠️ Ocorreu um erro: `{e}`\nTente novamente.",
            mrkdwn=True,
        )


# ── Comando /ajuda ───────────────────────────────────────────────────────────

@app.command("/ajuda")
async def handle_ajuda_command(ack, respond):
    await ack()
    await respond(text=AJUDA_TEXTO, response_type="ephemeral")


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
            text="Olá! 👋 Me pergunte algo sobre as campanhas.\nEx: _qual campanha tem melhor ROAS essa semana?_\n\nDigite `/ajuda` para ver exemplos de consultas.",
        )
        return

    # Detecta pedido de ajuda na menção
    if re.search(r"\bajuda\b|\bhelp\b|como\s+us[ao]r|como\s+consult|o\s+que\s+(voc[eê]|tu)\s+faz", text, re.I):
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=AJUDA_TEXTO,
            mrkdwn=True,
        )
        return

    # Detecta pedido de exportação
    formato_export = None
    if re.search(r"\bexcel\b|\bplanilha\b|\bxlsx\b", text, re.I):
        formato_export = "excel"
    elif re.search(r"\bpdf\b|\brelatório\b|\brelatorio\b", text, re.I):
        formato_export = "pdf"

    try:
        await client.reactions_add(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass

    await _processar_e_responder(text, channel, thread_ts, client,
                                  user_id=event.get("user"), formato_export=formato_export)

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

    # Detecta pedido de ajuda na DM
    if re.search(r"\bajuda\b|\bhelp\b|como\s+us[ao]r|como\s+consult|o\s+que\s+(voc[eê]|tu)\s+faz", text, re.I):
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=AJUDA_TEXTO,
            mrkdwn=True,
        )
        return

    # Detecta pedido de exportação
    formato_export = None
    if re.search(r"\bexcel\b|\bplanilha\b|\bxlsx\b", text, re.I):
        formato_export = "excel"
    elif re.search(r"\bpdf\b|\brelatório\b|\brelatorio\b", text, re.I):
        formato_export = "pdf"

    try:
        await client.reactions_add(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass

    await _processar_e_responder(text, channel, thread_ts, client,
                                  user_id=event.get("user"), formato_export=formato_export)

    try:
        await client.reactions_remove(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass
