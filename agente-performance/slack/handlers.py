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
import time

from slack_bolt.async_app import AsyncApp
from dotenv import load_dotenv

from agent.engine import consultar
from agent.claude_client import gerar_resposta
from agent.context_store import append_history, build_context_summary
from agent.conversation_log import registrar as log_registrar
from agent.cache import get as cache_get
from slack.formatter import markdown_to_blocks, truncar_blocos

AJUDA_TEXTO = """*⚙️ Agente de Performance Guará — Guia Rápido*

*⚡ Atalhos (slash commands)*

> `/hoje` — resumo do dia atual
> `/semana` — resumo dos últimos 7 dias
> `/mes` — mês atual · `/mes junho` — mês específico
> `/top` — top 5 campanhas por ROAS · `/top compras` · `/top 10`
> `/campanha ABO` — performance da campanha · `/campanha` (lista todas)
> `/campanhas` — lista completa de campanhas no banco
> `/alertas` — campanhas com ROAS ruim ou sem conversão
> `/funil` — funil geral · `/funil ABO` — funil de uma campanha
> `/compare ABO vs Advantage` — compara 2 campanhas lado a lado
> `/relatorio` — exporta Excel do mês · `/relatorio pdf` — em PDF
> `/ajuda` — este menu

*💬 Perguntas em linguagem natural*
Me mencione com @ ou mande uma DM:

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

*5. Comparação e histórico*
> _compare o ROAS de novembro vs maio_
> _como evoluiu o gasto da ABO dia a dia em junho?_
> _resuma tudo que aconteceu no segundo trimestre_

*💡 Dica:* combine campanha + métrica + período:
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
    """Núcleo: NLP → query banco → Claude → posta no Slack → salva histórico + log."""
    t_inicio = time.time()
    resposta_final = ""
    erro_final = None
    cache_hit = False

    try:
        # Verifica cache antes de tudo (para marcar cache_hit no log)
        cache_hit = cache_get(text) is not None

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
            resposta_final = erro
            erro_final = erro
            await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=erro, mrkdwn=True,
            )
            return

        # Dados vazios com sugestão de período
        if aviso_vazio:
            resposta_final = aviso_vazio
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
            resposta_final = f"Arquivo {filename} gerado"
            await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=f"✅ Arquivo *{filename}* gerado com sucesso!",
                mrkdwn=True,
            )
            return

        # Gera resposta conversacional com Claude
        resposta_final = gerar_resposta(
            pergunta=text, dados=dados, intent=intent, meta=meta,
            dados_extra=dados_extra, contexto=contexto,
        )

        append_history(thread_ts, channel, "agent", resposta_final[:500], meta)

        blocks = truncar_blocos(markdown_to_blocks(resposta_final))
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=resposta_final[:150], blocks=blocks,
        )

    except Exception as e:
        erro_final = str(e)
        logger.exception("Erro ao processar mensagem: %s", e)
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f"⚠️ Ocorreu um erro: `{e}`\nTente novamente.",
            mrkdwn=True,
        )

    finally:
        # Registra SEMPRE no log — mesmo em caso de erro ou cache hit
        latencia_ms = int((time.time() - t_inicio) * 1000)
        try:
            meta_log = resultado["meta"]  # type: ignore[name-defined]
        except (NameError, KeyError):
            meta_log = {}
        log_registrar(
            pergunta=text,
            resposta=resposta_final,
            meta=meta_log,
            user_id=user_id,
            channel=channel,
            thread_ts=thread_ts,
            latencia_ms=latencia_ms,
            cache_hit=cache_hit,
            erro=erro_final,
        )
        logger.info(
            "[LOG] user=%s intent=%s latencia=%dms cache=%s erro=%s",
            user_id, meta_log.get("intent", "?"), latencia_ms,
            cache_hit, erro_final is not None,
        )


# ── Comando /ajuda ───────────────────────────────────────────────────────────

@app.command("/ajuda")
async def handle_ajuda_command(ack, respond):
    await ack()
    await respond(text=AJUDA_TEXTO, response_type="ephemeral")


# ── /hoje — resumo do dia atual ───────────────────────────────────────────────

@app.command("/hoje")
async def handle_hoje(ack, respond, command, client):
    await ack()
    channel  = command["channel_id"]
    user_id  = command["user_id"]
    thread_ts = str(__import__("time").time())
    await respond(text="⏳ Buscando dados de hoje...", response_type="ephemeral")
    await _processar_e_responder(
        text="resumo geral de hoje",
        channel=channel, thread_ts=thread_ts, client=client, user_id=user_id,
    )


# ── /semana — resumo dos últimos 7 dias ───────────────────────────────────────

@app.command("/semana")
async def handle_semana(ack, respond, command, client):
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    thread_ts = str(__import__("time").time())
    await respond(text="⏳ Buscando dados da semana...", response_type="ephemeral")
    await _processar_e_responder(
        text="resumo geral dos últimos 7 dias",
        channel=channel, thread_ts=thread_ts, client=client, user_id=user_id,
    )


# ── /mes — resumo do mês atual ────────────────────────────────────────────────

@app.command("/mes")
async def handle_mes(ack, respond, command, client):
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    thread_ts = str(__import__("time").time())
    texto_arg = command.get("text", "").strip()
    # Aceita argumento: /mes junho ou /mes 2026-05
    if texto_arg:
        pergunta = f"resumo geral de {texto_arg}"
    else:
        pergunta = "resumo geral desse mês"
    await respond(text=f"⏳ Buscando dados: _{pergunta}_...", response_type="ephemeral")
    await _processar_e_responder(
        text=pergunta,
        channel=channel, thread_ts=thread_ts, client=client, user_id=user_id,
    )


# ── /top — top campanhas por ROAS ─────────────────────────────────────────────

@app.command("/top")
async def handle_top(ack, respond, command, client):
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    thread_ts = str(__import__("time").time())
    arg = command.get("text", "").strip().lower()

    # Exemplos: /top roas · /top compras · /top 5 roas · /top
    import re as _re
    n_match = _re.search(r"(\d+)", arg)
    n       = n_match.group(1) if n_match else "5"

    if "compra" in arg or "venda" in arg:
        metrica  = "compras"
        pergunta = f"top {n} campanhas por compras esse mês"
    else:
        metrica  = "ROAS"
        pergunta = f"top {n} campanhas por ROAS esse mês"

    await respond(text=f"⏳ Buscando top {n} por {metrica}...", response_type="ephemeral")
    await _processar_e_responder(
        text=pergunta,
        channel=channel, thread_ts=thread_ts, client=client, user_id=user_id,
    )


# ── /campanha <nome> — performance da campanha ────────────────────────────────

@app.command("/campanha")
async def handle_campanha(ack, respond, command, client):
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    thread_ts = str(__import__("time").time())
    nome = command.get("text", "").strip()

    if not nome:
        from agent.queries import listar_campanhas
        campanhas = listar_campanhas()
        lista = "\n".join(
            f"  • `{c['campanha']}` — R$ {c['investimento_total']:,.0f}"
            for c in campanhas[:15]
        )
        await respond(
            text=f"*Campanhas disponíveis (top 15 por investimento):*\n{lista}\n\n_Use `/campanha <nome>` para ver a performance._",
            response_type="ephemeral",
        )
        return

    await respond(text=f"⏳ Buscando performance de *{nome}*...", response_type="ephemeral")
    await _processar_e_responder(
        text=f"performance da campanha {nome} esse mês",
        channel=channel, thread_ts=thread_ts, client=client, user_id=user_id,
    )


# ── /alertas — campanhas com ROAS ruim ────────────────────────────────────────

@app.command("/alertas")
async def handle_alertas(ack, respond, command, client):
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    thread_ts = str(__import__("time").time())
    await respond(text="⏳ Verificando alertas de performance...", response_type="ephemeral")
    await _processar_e_responder(
        text="quais campanhas estão com ROAS ruim ou sem conversão nos últimos 30 dias?",
        channel=channel, thread_ts=thread_ts, client=client, user_id=user_id,
    )


# ── /funil [campanha] — métricas de funil ────────────────────────────────────

@app.command("/funil")
async def handle_funil(ack, respond, command, client):
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    thread_ts = str(__import__("time").time())
    nome = command.get("text", "").strip()

    if nome:
        pergunta = f"funil de conversão da campanha {nome} esse mês"
    else:
        pergunta = "funil de conversão geral esse mês"

    await respond(text=f"⏳ Montando funil: _{pergunta}_...", response_type="ephemeral")
    await _processar_e_responder(
        text=pergunta,
        channel=channel, thread_ts=thread_ts, client=client, user_id=user_id,
    )


# ── /compare <campA> vs <campB> — comparação direta ──────────────────────────

@app.command("/compare")
async def handle_compare(ack, respond, command, client):
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    thread_ts = str(__import__("time").time())
    arg = command.get("text", "").strip()

    if not arg:
        await respond(
            text="💡 Use: `/compare ABO vs Advantage` ou `/compare ABO Advantage`",
            response_type="ephemeral",
        )
        return

    await respond(text=f"⏳ Comparando: _{arg}_...", response_type="ephemeral")
    await _processar_e_responder(
        text=f"compare {arg} esse mês",
        channel=channel, thread_ts=thread_ts, client=client, user_id=user_id,
    )


# ── /relatorio [excel|pdf] — exporta relatório ───────────────────────────────

@app.command("/relatorio")
async def handle_relatorio(ack, respond, command, client):
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    thread_ts = str(__import__("time").time())
    arg = command.get("text", "").strip().lower()

    if "pdf" in arg:
        formato = "pdf"
    else:
        formato = "excel"  # padrão

    await respond(
        text=f"⏳ Gerando relatório {formato.upper()} do mês atual...",
        response_type="ephemeral",
    )
    await _processar_e_responder(
        text=f"resumo geral desse mês em {formato}",
        channel=channel, thread_ts=thread_ts, client=client, user_id=user_id,
        formato_export=formato,
    )


# ── /campanhas — lista todas as campanhas ─────────────────────────────────────

@app.command("/campanhas")
async def handle_campanhas(ack, respond):
    await ack()
    from agent.queries import listar_campanhas
    campanhas = listar_campanhas()
    linhas = ["*📂 Campanhas cadastradas no banco de dados:*\n"]
    for i, c in enumerate(campanhas, 1):
        invest = f"R$ {c['investimento_total']:,.0f}" if c['investimento_total'] else "—"
        linhas.append(
            f"{i}. *{c['campanha']}*"
            f" | invest: {invest}"
            f" | ativo: {c['primeira_data']} → {c['ultima_data']}"
        )
    texto = "\n".join(linhas)
    await respond(text=texto, response_type="ephemeral")


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
