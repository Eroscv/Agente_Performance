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
from db.consultas_log import registrar_consulta, aprovar_consulta as _aprovar_consulta_log
from agent.conversation_log import registrar as log_registrar
from agent.cache import get as cache_get
from slack.formatter import markdown_to_blocks, truncar_blocos
from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n, roas_emoji

logger = logging.getLogger(__name__)

# ── Utilitários e núcleo movidos para módulos dedicados ───────────────────────
from slack.helpers import (
    _detectar_formato_export, formatar_resumo_relatorio, _sugestao_proativa,
    _verificar_cobertura_dados_handler, _kb_local_salvar, _kb_local_aprovar,
    _ultimo_dia_com_dados, _verificar_sync_falhou,
    AJUDA_TEXTO, _CLIENTES_NOTION, _HANDLER_TIMEOUT,
)
from slack.responder import _processar_e_responder

# ── Fluxo de criação manual de cards no Notion via Slack ─────────────────────
import sys as _sys
_sys.path.insert(0, "/home/ubuntu/.openclaw/workspace/transcricao")
from slack_criar_card import (
    registrar_handlers as _registrar_criar_card,
    handle_message_criar_card as _handle_criar_card_msg,
    cmd_tutorial_transcricao as _cmd_tut_transcricao,
)
from slack_landing_page import (
    registrar_handlers_landing as _registrar_landing,
    handle_message_landing as _handle_landing_msg,
    cmd_tutorial_landing as _cmd_tut_landing,
    cmd_criar_landing as _cmd_criar_landing,
)

# ── Interceptor de pseudo-slash-commands via mensagem de texto ─────────────────
# Permite usar /comando como texto (mencionando o bot ou em DM)
# sem precisar de cadastro no Slack App Dashboard.
async def _interceptar_slash(text: str, user_id: str, channel: str,
                              thread_ts: str, client, say=None) -> bool:
    """
    Detecta '/comando [args]' no texto e executa o handler direto.
    Retorna True se consumiu a mensagem.
    """
    m = re.match(r"^/([a-z][a-z0-9-]*)(?:\s+(.*))?$", text.strip(), re.I)
    if not m:
        return False

    cmd  = m.group(1).lower()
    args = (m.group(2) or "").strip()

    # respond fake: posta no canal, ancorado na thread (visível a todos)
    class _Respond:
        async def __call__(self_, text="", blocks=None, response_type="", **kw):
            payload = {
                "channel":    channel,
                "thread_ts":  thread_ts,
                "mrkdwn":     True,
            }
            if blocks:
                payload["blocks"] = blocks
                payload["text"]   = text or "."
            else:
                payload["text"]   = text
            await client.chat_postMessage(**payload)
    _respond = _Respond()

    # command fake para handlers que precisam de command dict
    _command = {"user_id": user_id, "channel_id": channel, "text": args, "thread_ts": thread_ts}

    # ack fake assíncrono
    async def _ack(): pass

    # say ancorado na thread
    async def _say_anchored(**kwargs):
        if "channel" not in kwargs:
            kwargs["channel"]   = channel
        kwargs.setdefault("thread_ts", thread_ts)
        await client.chat_postMessage(**kwargs)

    handlers = {
        "tutorial":              lambda: _run_tut_performance(_respond),
        "tutorial-transcricao": lambda: _cmd_tut_transcricao(_ack, _respond),
        "tutorial-landing":     lambda: _cmd_tut_landing(_ack, _respond),
        "criar-landing":        lambda: _cmd_criar_landing(_ack, _command, _say_anchored),
        "ajuda":                lambda: client.chat_postMessage(
                                    channel=channel, thread_ts=thread_ts,
                                    text=AJUDA_TEXTO, mrkdwn=True),
    }

    if cmd not in handlers:
        return False

    try:
        await handlers[cmd]()
    except Exception as e:
        logger.exception("Erro no pseudo-slash /%s: %s", cmd, e)
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f"⚠️ Erro ao executar `/{cmd}`: {e}", mrkdwn=True
        )
    return True


def _make_say(client, channel):
    """Cria uma função say simples para handlers que precisam dela."""
    async def _say(**kwargs):
        if "channel" not in kwargs:
            kwargs["channel"] = channel
        await client.chat_postMessage(**kwargs)
    return _say


async def _run_tut_performance(respond):
    """Executa o corpo do /tutorial sem depender do decorator @app.command."""
    async def _ack(): pass
    await handle_tutorial(_ack, respond)

app = AsyncApp(
    token=os.getenv("SLACK_BOT_TOKEN", ""),
    signing_secret=os.getenv("SLACK_SIGNING_SECRET", ""),
)

# Registra handlers do fluxo /criar-card
_registrar_criar_card(app)
# Registra handlers do Agente de Landing Pages
_registrar_landing(app)


def get_slack_app() -> AsyncApp:
    return app


def _limpar_mencao(text: str, bot_user_id: str | None) -> str:
    # Remove qualquer <@USER_ID> (bot ou usuário) do início da mensagem
    text = re.sub(r"<@[A-Z0-9]+>", "", text)
    return text.strip()


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


# ── /relatorio [excel|pdf|notion|tudo] — exporta relatório ──────────────────────────────

@app.command("/relatorio")
async def handle_relatorio(ack, respond, command, client):
    """
    /relatorio                  — relatório semanal Source OF (semana passada)
    /relatorio pod              — relatório semanal POD Kombucha  (item 7)
    /relatorio pod mes          — relatório mensal POD Kombucha
    /relatorio semana           — semana passada Source OF
    /relatorio mes              — mês passado Source OF
    /relatorio hoje             — dia de hoje
    /relatorio 2026-06-01 2026-06-30  — período customizado
    """
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    arg       = command.get("text", "").strip().lower()

    # Detecta cliente POD pelo argumento
    import re as _re
    is_pod    = bool(_re.search(r"\b(pod|kombucha)\b", arg))
    arg_clean = _re.sub(r"\b(pod|kombucha)\b", "", arg).strip()
    cliente_nome = "POD Kombucha" if is_pod else "Source OF"

    await respond(
        text=f"⏳ Gerando relatório *{cliente_nome}* e publicando no Notion...",
        response_type="ephemeral",
    )

    try:
        import asyncio
        from datetime import date, timedelta

        hoje = date.today()
        data_inicio = data_fim = None
        usar_mensal = False

        partes = arg_clean.split()
        if len(partes) == 2:
            try:
                data_inicio = date.fromisoformat(partes[0])
                data_fim    = date.fromisoformat(partes[1])
            except ValueError:
                pass

        if not data_inicio:
            if "mes" in arg_clean or "mês" in arg_clean:
                usar_mensal = True
            elif "hoje" in arg_clean:
                data_inicio = data_fim = hoje
            elif "ontem" in arg_clean:
                data_inicio = data_fim = hoje - timedelta(days=1)
            else:
                seg = hoje - timedelta(days=hoje.weekday() + 7)
                data_inicio = seg
                data_fim    = seg + timedelta(days=6)

        loop = asyncio.get_event_loop()

        if is_pod:
            from relatorio.builder_pod import gerar_relatorio_pod
            resultado = await loop.run_in_executor(
                None,
                lambda: gerar_relatorio_pod(
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                    tipo="mensal" if usar_mensal else "semanal",
                    publicar=True,
                )
            )
        elif usar_mensal:
            from relatorio.builder_mensal import gerar_relatorio_mensal
            resultado = await loop.run_in_executor(
                None, lambda: gerar_relatorio_mensal(cliente="source_of", publicar=True)
            )
        else:
            from relatorio.builder_semanal import gerar_relatorio_semanal
            resultado = await loop.run_in_executor(
                None,
                lambda: gerar_relatorio_semanal(
                    cliente="source_of",
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                    publicar=True,
                )
            )

        if "erro" in resultado:
            await client.chat_postMessage(
                channel=channel,
                text=f"❌ Erro ao gerar relatório: {resultado['erro']}",
            )
            return

        # Reutiliza formatar_resumo_relatorio (compartilhado com cron_relatorio.py)
        msg = formatar_resumo_relatorio(resultado)
        await client.chat_postMessage(channel=channel, text=msg, mrkdwn=True)

    except Exception as e:
        logger.exception("Erro no /relatorio: %s", e)
        await client.chat_postMessage(
            channel=channel,
            text=f"⚠️ Erro ao gerar relatório: `{e}`",
        )


# ── /campanhas — lista todas as campanhas ─────────────────────────────────────



# ── /pod — consulta POD Kombucha diretamente (item 9) ────────────────────────

@app.command("/pod")
async def handle_pod(ack, respond, command, client):
    """
    /pod                     — resumo semanal POD Kombucha
    /pod status              — KPIs do dia mais recente disponível (item 4)
    /pod essa semana         — semana atual
    /pod mes                 — mês atual
    /pod qual campanha...    — pergunta livre
    """
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    arg       = command.get("text", "").strip() or "como foi essa semana?"
    thread_ts = str(__import__("time").time())

    # /pod status — resposta instantânea sem Claude (item 4)
    if arg.strip().lower() in ("status", "hoje", "agora"):
        try:
            import sqlite3 as _sq
            from db.connections import get_federated_conn as _gcc
            _c = _gcc()
            _c.row_factory = _sq.Row
            _last = _c.execute(
                "SELECT MAX(data) AS ultimo FROM pod_kombucha_gads_diario"
            ).fetchone()
            _ultimo = _last["ultimo"] if _last else None
            if _ultimo:
                from datetime import date as _d
                _obj = _d.fromisoformat(_ultimo)
                _DIAS = ["Segunda","Terça","Quarta","Quinta","Sexta","Sábado","Domingo"]
                _nome = _DIAS[_obj.weekday()]
                _row = _c.execute("""
                    SELECT
                        SUM(investimento)   AS invest,
                        SUM(valor_conversao) AS receita,
                        CASE WHEN SUM(investimento)>0
                             THEN SUM(valor_conversao)/SUM(investimento) END AS roas,
                        SUM(conversoes) AS conversoes,
                        CASE WHEN SUM(impressoes)>0
                             THEN CAST(SUM(cliques) AS REAL)/SUM(impressoes)*100 END AS ctr
                    FROM pod_kombucha_gads_diario WHERE data = ?
                """, (_ultimo,)).fetchone()
                def _brl(v): return f"R$ {v:,.2f}".replace(",","X").replace(".",",").replace("X",".") if v else "—"
                def _x(v): return f"{v:.2f}x" if v else "—"
                msg = (
                    f"📊 *POD Kombucha · Status — {_nome} {_obj.strftime('%d/%m/%Y')}*\n"
                    f"```\n"
                    f"Investimento  │ {_brl(_row['invest'])}\n"
                    f"Receita       │ {_brl(_row['receita'])}\n"
                    f"ROAS          │ {_x(_row['roas'])}\n"
                    f"Conversões    │ {_row['conversoes'] or 0:.1f}\n"
                    f"CTR           │ {_row['ctr'] or 0:.2f}%\n"
                    f"```\n"
                    f"_Dado mais recente disponível no banco_"
                )
            else:
                msg = "⚠️ Nenhum dado POD encontrado no banco."
            await client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=msg, mrkdwn=True)
        except Exception as _e:
            await client.chat_postMessage(channel=channel, text=f"⚠️ Erro: {_e}")
        return

    await respond(text=f"⏳ Consultando POD Kombucha: _{arg}_...", response_type="ephemeral")
    await _processar_e_responder(
        text=f"POD Kombucha {arg}",
        channel=channel, thread_ts=thread_ts, client=client, user_id=user_id,
    )

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
    # Remove backticks que o Slack adiciona quando o usuário formata como código
    text      = text.strip("`")
    channel   = event["channel"]
    user_id   = event.get("user", "")
    thread_ts = event.get("thread_ts") or event["ts"]
    files     = event.get("files", [])

    logger.info("[mention] user=%s text=%r", user_id, text[:80])

    # say ancorado na thread da menção
    async def _say_mention(**kwargs):
        kwargs.setdefault("channel", channel)
        kwargs.setdefault("thread_ts", thread_ts)
        await client.chat_postMessage(**kwargs)

    # ── Pseudo-slash PRIMEIRO: /comando digitado como menção ──────────────────────
    if text.startswith("/"):
        if await _interceptar_slash(text, user_id, channel, thread_ts, client, say):
            return

    # ── Intercepta fluxos conversacionais ativos ────────────────────────────────
    if await _handle_criar_card_msg(user_id, text, _say_mention, channel, thread_ts):
        return
    if await _handle_landing_msg(user_id, text, _say_mention, channel, thread_ts):
        return
    # ─────────────────────────────────────────────────────────────────────────────

    # Se tem imagem anexada, processa independente de ter texto ou não
    if files and any(f.get("mimetype", "").startswith("image/") for f in files):
        try:
            await client.reactions_add(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
        except Exception:
            pass
        await _processar_e_responder(
            text, channel, thread_ts, client,
            user_id=event.get("user"), files=files,
        )
        try:
            await client.reactions_remove(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
        except Exception:
            pass
        return

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

    # Detecta pedido de exportação (excel / pdf / notion / excel+notion)
    formato_export, cliente_notion = _detectar_formato_export(text)

    try:
        await client.reactions_add(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass

    await _processar_e_responder(text, channel, thread_ts, client,
                                  user_id=event.get("user"), formato_export=formato_export, cliente_notion=cliente_notion)

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

    text      = event.get("text", "").strip().strip("`")
    channel   = event["channel"]
    user_id   = event.get("user", "")
    thread_ts = event.get("thread_ts") or event["ts"]

    async def _say_dm(**kwargs):
        await client.chat_postMessage(channel=channel, **kwargs)

    logger.info("[dm] user=%s text=%r", user_id, text[:80])

    # ── Pseudo-slash PRIMEIRO: /comando digitado em DM ────────────────────────
    if text.startswith("/"):
        if await _interceptar_slash(text, user_id, channel, channel, client, _say_dm):
            return

    # ── Intercepta fluxos conversacionais ativos ──────────────────────────────
    if await _handle_criar_card_msg(user_id, text, _say_dm, channel, thread_ts):
        return
    if await _handle_landing_msg(user_id, text, _say_dm, channel, thread_ts):
        return
    # ─────────────────────────────────────────────────────────────────────────

    files     = event.get("files", [])

    # Se tem imagem anexada, processa independente de ter texto ou não
    if files and any(f.get("mimetype", "").startswith("image/") for f in files):
        try:
            await client.reactions_add(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
        except Exception:
            pass
        await _processar_e_responder(
            text, channel, thread_ts, client,
            user_id=event.get("user"), files=files,
        )
        try:
            await client.reactions_remove(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
        except Exception:
            pass
        return

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

    # Detecta pedido de exportação (excel / pdf / notion / excel+notion)
    formato_export, cliente_notion = _detectar_formato_export(text)

    try:
        await client.reactions_add(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass

    await _processar_e_responder(text, channel, thread_ts, client,
                                  user_id=event.get("user"), formato_export=formato_export, cliente_notion=cliente_notion)

    try:
        await client.reactions_remove(channel=channel, timestamp=event["ts"], name="hourglass_flowing_sand")
    except Exception:
        pass


# ── Evento: reação 👍 — aprovação na Base de Conhecimento ───────────────────

@app.event("reaction_added")
async def handle_reaction(event, client):
    """
    Quando alguém dá 👍 (+1) em uma resposta do bot:
    - Encontra a entrada correspondente na Base de Conhecimento do Notion
    - Marca como aprovada e incrementa frequência
    - Reage com ✅ para confirmar
    """
    reaction = event.get("reaction", "")
    if reaction not in ("+1", "thumbsup", "white_check_mark"):
        return  # Ignora outras reações

    item = event.get("item", {})
    if item.get("type") != "message":
        return

    msg_ts      = item.get("ts")
    channel     = item.get("channel")
    reactor_id  = event.get("user")

    try:
        # Busca a mensagem original para confirmar que é do bot
        result = await client.conversations_history(
            channel=channel,
            oldest=msg_ts,
            latest=msg_ts,
            inclusive=True,
            limit=1,
        )
        messages = result.get("messages", [])
        if not messages:
            return

        msg      = messages[0]
        bot_id   = msg.get("bot_id")
        if not bot_id:
            return  # Reação não é em mensagem do bot

        # Busca no KB pelo ts da mensagem
        from notion.knowledge_base import buscar_por_slack_ts, aprovar_entrada
        page_id = buscar_por_slack_ts(msg_ts)

        if page_id:
            # Busca nome do usuário para registrar quem aprovou
            try:
                user_info  = await client.users_info(user=reactor_id)
                nome       = user_info["user"]["real_name"] or user_info["user"]["name"]
            except Exception:
                nome = reactor_id or "Slack"

            ok = aprovar_entrada(page_id, aprovado_por=nome)
            if ok:
                logger.info("KB: resposta aprovada por %s (ts=%s)", nome, msg_ts)
                # Confirma com ✅ na mensagem
                try:
                    await client.reactions_add(
                        channel=channel, timestamp=msg_ts, name="white_check_mark"
                    )
                except Exception:
                    pass
        else:
            logger.debug("KB: nenhuma entrada encontrada para ts=%s", msg_ts)

        # MELHORIA 9: Aprova também na tabela local kb_cache
        try:
            aprovado_local = _kb_local_aprovar(msg_ts)
            if aprovado_local:
                logger.info("KB local: entrada aprovada (ts=%s)", msg_ts)
        except Exception as e_kbl:
            logger.debug("KB local: erro ao aprovar (não crítico): %s", e_kbl)

        # Marca consulta como aprovada em consultas_usuarios
        try:
            _aprovar_consulta_log(msg_ts)
        except Exception as e_cu:
            logger.debug("[ConsultasLog] erro ao aprovar (não crítico): %s", e_cu)

    except Exception as e:
        logger.debug("KB reaction handler erro (não crítico): %s", e)


# ── Comandos auxiliares ────────────────────────────────────────────────────────

@app.command("/status")
async def handle_status(ack, respond):
    """Status rápido do sistema — banco, cache, KB."""
    await ack()
    import sqlite3
    from agent.cache import _cache  # type: ignore
    from db.connections import LEGACY_DB_PATH as DB_PATH

    # Banco
    try:
        conn = sqlite3.connect(DB_PATH)
        row  = conn.execute("SELECT COUNT(*), MIN(data), MAX(data) FROM v_performance").fetchone()
        conn.close()
        db_info = f"*{row[0]:,}* registros | {row[1]} → {row[2]}"
    except Exception:
        db_info = "⚠️ erro ao consultar"

    # Cache
    cache_size = len(_cache) if hasattr(_cache, '__len__') else "?"

    # KB Notion
    try:
        from notion.knowledge_base import listar_entradas
        kb = listar_entradas(limit=100)
        aprovadas = sum(1 for e in kb if e["aprovado"])
        kb_info = f"*{len(kb)}* entradas | *{aprovadas}* aprovadas"
    except Exception:
        kb_info = "⚠️ erro ao consultar"

    texto = f"""\
*⚙️ Status — Agente de Performance Source OF*

• 🗄️ *Banco:* {db_info}
• ⚡ *Cache:* {cache_size} entradas ativas
• 🧠 *Base de Conhecimento:* {kb_info}
• 🤖 *Modelo:* `{os.getenv('CLAUDE_MODEL', 'claude-haiku-4-5-20251001')}`
• ✅ *Serviço:* online | porta 8001
"""
    await respond(text=texto, response_type="ephemeral")


@app.command("/exemplos")
async def handle_exemplos(ack, respond):
    """Mostra exemplos práticos de perguntas em linguagem natural."""
    await ack()
    texto = """\
*💡 Exemplos de perguntas — Agente de Performance Source OF*

*📊 Resumos por período*
• `resumo de junho`
• `como foi a semana passada?`
• `dados dos últimos 30 dias`
• `resultado de maio vs junho`

*🎯 Por campanha*
• `como está a ABO esse mês?`
• `performance da Advantage em junho`
• `compare ABO vs Advantage`
• `qual campanha teve melhor ROAS?`

*🏆 Rankings*
• `top 5 campanhas por ROAS`
• `quais geraram mais compras em 2026?`
• `ranking de eficiência das campanhas`

*🔍 Funil & Conversão*
• `mostre o funil geral`
• `qual a taxa de conversão da ABO?`
• `quantos carrinhos abandonados essa semana?`

*🚨 Alertas*
• `tem alguma campanha com ROAS ruim?`
• `alguma campanha sem conversão nos últimos 7 dias?`

*📁 Exportação*
• `gera planilha Excel do mês`
• `exportar no Notion`
• `relatório de junho em PDF`

*💬 Dica:* Use `/ajuda` para ver todos os slash commands disponíveis.
"""
    await respond(text=texto, response_type="ephemeral")


@app.command("/metricas")
async def handle_metricas(ack, respond):
    """Lista todas as métricas disponíveis para consulta."""
    await ack()
    texto = """\
*📐 Métricas disponíveis — Source OF Meta Ads*

*💰 Investimento & Receita*
• `investimento` — valor gasto (R$)
• `receita` / `valor_compras` — receita gerada (R$)
• `roas` — retorno sobre investimento (receita ÷ investimento)
• `custo_por_compra` — CPO (R$ por compra)

*🛒 Conversões*
• `compras` — número de compras
• `adicoes_carrinho` — adds ao carrinho
• `checkout_iniciado` — checkouts iniciados
• `taxa_conversao` — % de cliques → compra

*👁️ Alcance & Visibilidade*
• `impressoes` — impressões totais
• `alcance` — pessoas únicas alcançadas
• `frequencia` — média de vezes que a mesma pessoa viu (> 4 = fadiga)
• `cpm` — custo por mil impressões (R$)

*🖱️ Engajamento*
• `cliques_link` — cliques no link
• `ctr` — taxa de cliques (%)
• `cpc` — custo por clique (R$)
• `visualizacoes_pagina` — visualizações da landing page

*📅 Filtros temporais*
• `hoje` · `ontem` · `essa semana` · `semana passada`
• `esse mês` · `mês passado` · `últimos 7/30/90 dias`
• `em junho` · `em 2025` · `01/06/2026`
"""
    await respond(text=texto, response_type="ephemeral")


@app.command("/kb")
async def handle_kb(ack, respond):
    """Lista as últimas entradas aprovadas na Base de Conhecimento."""
    await ack()
    try:
        from notion.knowledge_base import listar_entradas
        entradas = listar_entradas(apenas_aprovadas=False, limit=10)
        if not entradas:
            await respond(text="📚 Base de Conhecimento vazia. Dê 👍 em respostas que gostou para aprová-las!", response_type="ephemeral")
            return

        linhas = ["*🧠 Base de Conhecimento — últimas 10 entradas*\n"]
        for e in entradas:
            status = "✅" if e["aprovado"] else "⏳"
            freq   = f"x{e['frequencia']}" if e["frequencia"] > 1 else ""
            linhas.append(f"{status} *{e['intent']}* {freq} — _{e['pergunta'][:60]}_")

        linhas.append(f"\n_Dê 👍 em qualquer resposta do bot para aprová-la na base de conhecimento._")
        await respond(text="\n".join(linhas), response_type="ephemeral")
    except Exception as e:
        await respond(text=f"⚠️ Erro ao consultar KB: `{e}`", response_type="ephemeral")


@app.command("/cache-limpar")
async def handle_cache_limpar(ack, respond):
    """Limpa o cache de consultas (força nova busca no banco)."""
    await ack()
    try:
        from agent.cache import _cache
        size = len(_cache)
        _cache.clear()
        await respond(text=f"🗑️ Cache limpo — {size} entradas removidas. Próximas consultas buscarão dados frescos do banco.", response_type="ephemeral")
    except Exception as e:
        await respond(text=f"⚠️ Erro: `{e}`", response_type="ephemeral")


@app.command("/tutorial")
async def handle_tutorial(ack, respond):
    """Tutorial do Agente de Performance Guará."""
    await ack()
    texto = """\
*🎓 Tutorial — Agente de Performance Guará*
_Consultas de mídia paga em linguagem natural._

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*O que é este agente?*

Monitora continuamente os dados de mídia paga dos clientes Guará (Meta Ads, Google Ads e Shopify) e responde perguntas sobre métricas, campanhas, alertas e resultados.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*🗣️ Como falar com o agente*

• Em qualquer canal: `@agente [sua pergunta]`
• DM direto para o bot

*Teste agora:* `@agente como foi essa semana?`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📊 Consultas de performance*

*Resumos por período*
`@agente resumo de outubro` — investimento, receita, ROAS do mês
`@agente como foi a semana passada?`
`@agente resultado de hoje`
`/hoje` · `/semana` · `/mes`

*Rankings*
`@agente top 5 campanhas por ROAS esse mês`
`@agente quais campanhas geraram mais compras em outubro?`
`@agente qual anúncio tem melhor CTR na Source OF?`
`/top`

*Comparações*
`@agente compara outubro vs setembro`
`@agente POD vs Source OF em novembro`
`/compare X vs Y`

*Funil de conversão*
`@agente mostra o funil de outubro para Source OF`
`/funil`

*Evolução temporal*
`@agente como evoluiu o ROAS da Advantage dia a dia?`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*🚨 Alertas e diagnósticos*

`@agente tem alguma campanha com ROAS abaixo do esperado?`
`@agente campanhas sem conversão nos últimos 7 dias`
`@agente o que está puxando o resultado para baixo?`
`/alertas` — lista todos os alertas ativos agora

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*🏢 Clientes disponíveis*

🔵 *Source OF* — Meta Ads + Google Ads + Shopify
🟢 *POD Kombucha* — Meta Ads + Google Ads + Shopify
🟠 *Botica Ouro Preto* — Meta Ads + Shopify

`@agente resumo da POD essa semana`
`@agente como está a Botica em outubro?`
`/pod` — resumo rápido da POD

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📤 Exportar dados*

`@agente relatório de outubro em Excel`
`@agente exporta resumo da semana em PDF`
`@agente exporta para o Notion o relatório de outubro`
`/relatorio` · `/relatorio notion`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*⚡ Slash Commands*
`/tutorial` · `/ajuda` · `/exemplos`
`/hoje` · `/semana` · `/mes` · `/top` · `/alertas`
`/campanha [nome]` · `/compare X vs Y` · `/funil`
`/pod` · `/campanhas` · `/metricas` · `/relatorio`
`/status` · `/kb`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*Outros agentes*
`/tutorial-landing` — geração de landing pages com Claude
`/tutorial-transcricao` — atas de reunião → Notion

*💡 Dicas*
• Dê 👍 nas respostas certas — o agente aprende
• Dados atualizados a cada 6h — `/status` mostra a última sync
_Só você está vendo esta mensagem._
"""
    await respond(text=texto, response_type="ephemeral")
