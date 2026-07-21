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

# Timeout global para _processar_e_responder (segundos)
_HANDLER_TIMEOUT = int(os.getenv("SLACK_HANDLER_TIMEOUT", "60"))

# Dict de clientes Notion — elevado para módulo para evitar reconstrução a cada chamada
_CLIENTES_NOTION: dict[str, str] = {
    "consorcio":         "consorcio_sorteado",
    "consórcio":         "consorcio_sorteado",
    "sorteado":          "consorcio_sorteado",
    "source":            "source_of",
    "source of":         "source_of",
    "máximo":            "maximo_servico",
    "maximo":            "maximo_servico",
    "serviço":           "maximo_servico",
    "mushin":            "be_mushin",
    "be mushin":         "be_mushin",
    "bazar":             "bazar_precatorio",
    "precatório":        "bazar_precatorio",
    "precatorio":        "bazar_precatorio",
}

AJUDA_TEXTO = """
*⚙️ Agente de Performance Guará — Source OF*

Sou seu analista de mídia paga. Me pergunte qualquer coisa sobre as campanhas da Source OF no Meta Ads — ou use os atalhos abaixo.

──────────────────────────────────────────────────
*💬 Como usar*

Mencione `@agente` em qualquer canal ou mande uma DM:
> _@agente como foi o mês de junho?_

Ou use os slash commands direto:
> `/hoje` · `/semana` · `/top` · `/alertas` · `/ajuda`

──────────────────────────────────────────────────
*🟢 10 Exemplos práticos*

*1.* _Qual campanha teve melhor ROAS essa semana?_
*2.* _Resumo completo de junho — investimento, receita e compras_
*3.* _Como foi a ABO nos últimos 30 dias?_
*4.* _Compare a Advantage vs ABO em maio_
*5.* _Qual campanha está com ROAS abaixo de 1?_ *(alertas)*
*6.* _Mostre o funil de conversão da ABO em junho_
*7.* _Qual o custo por compra médio esse mês?_
*8.* _Top 3 campanhas que geraram mais compras em 2026_
*9.* _Como evoluiu o investimento da Advantage dia a dia em junho?_
*10.* _Tem alguma campanha com alta frequência que pode estar saturando?_

──────────────────────────────────────────────────
*⚡ Slash Commands*

`/hoje` — resumo do dia
`/semana` — últimos 7 dias
`/mes` — mês atual · `/mes junho` — mês específico
`/top` — top 5 por ROAS · `/top compras` · `/top 10`
`/campanha ABO` — detalhe de uma campanha
`/campanhas` — lista todas as campanhas
`/compare ABO vs Advantage` — comparação lado a lado
`/funil` — funil de conversão · `/funil ABO` — de uma campanha
`/alertas` — campanhas com problema de ROAS ou conversão
`/relatorio` — relatório semanal (semana passada) no Notion · `/relatorio mes` · `/relatorio notion`
`/exemplos` — mais exemplos de perguntas
`/metricas` — lista de todas as métricas disponíveis
`/status` — status do sistema
`/ajuda` — este menu

──────────────────────────────────────────────────
*📅 Períodos que entendo*
`hoje` · `essa semana` · `semana passada` · `esse mês` · `mês passado`
`últimos 7/30/90 dias` · `em junho` · `em 2025` · `black friday`
`01/06/2026` · `segundo trimestre` · `janeiro a março`

*👍 Dê 👍 nas respostas que gostar — entram para a memória do agente!*
"""

def _detectar_formato_export(text: str) -> tuple[str | None, str | None]:
    """
    Detecta se o usuário quer exportar dados e para qual formato.
    Retorna (formato_export, cliente_notion).
    
    Formatos:
      'excel'        — gera planilha .xlsx
      'pdf'          — gera PDF
      'notion'       — publica página no Notion
      'excel+notion' — envia o .xlsx E publica no Notion
    """
    tem_excel  = bool(re.search(r"\bexcel\b|\bplanilha\b|\bxlsx\b", text, re.I))
    tem_pdf    = bool(re.search(r"\bpdf\b", text, re.I))
    tem_notion = bool(re.search(r"\bnotion\b|\bpubli[cq]|\bpublic[ae]\.?\b|salvar no notion|enviar (para|pro) notion", text, re.I))

    formato = None
    if tem_excel and tem_notion:
        formato = "excel+notion"
    elif tem_notion:
        formato = "notion"
    elif tem_excel:
        formato = "excel"
    elif tem_pdf:
        formato = "pdf"

    # Detecta cliente do Notion no texto usando o dict de módulo
    cliente = None
    t = text.lower()
    for token, key in _CLIENTES_NOTION.items():
        if token in t:
            cliente = key
            break

    return formato, cliente


def _brl(v) -> str:
    """Formata valor como BRL."""
    if v is None:
        return "—"
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_resumo_relatorio(resultado: dict) -> str:
    """
    Formata o resumo de um resultado de relatório (semanal ou mensal) como texto Slack.
    Reutilizada por /relatorio (handlers.py) e cron_relatorio.py.

    Args:
        resultado: dict retornado por gerar_relatorio_semanal / gerar_relatorio_mensal

    Returns:
        String formatada para Slack (mrkdwn)
    """
    r           = resultado.get("resumo", {})
    periodo_str = resultado.get("periodo", "")
    url         = resultado.get("url") or f"https://notion.so/{resultado.get('page_id','').replace('-','')}"
    alertas     = resultado.get("alertas", [])
    alerta_txt  = "\n".join(f"  {a}" for a in alertas)

    roas_str = f"{r['roas']:.2f}x" if r.get('roas') else '—'
    ctr_str  = f"{r['ctr']:.2f}%" if r.get('ctr') else '—'

    return (
        f"✅ *Relatório publicado no Notion!*\n\n"
        f"📅 *Período:* {periodo_str}\n"
        f"📊 *Campanhas:* {resultado.get('num_campanhas')}\n"
        f"💰 *Investimento:* {_brl(r.get('total_invest'))}  (+imposto: {_brl(r.get('invest_imp'))})\n"
        f"📈 *ROAS:* {roas_str}\n"
        f"🛒 *Compras:* {r.get('total_compras', '—')}\n"
        f"💵 *CPA:* {_brl(r.get('cpa'))}\n"
        f"🎯 *CTR:* {ctr_str}\n\n"
        f"🚨 *Alertas:*\n{alerta_txt}\n\n"
        f"📎 <{url}|Abrir relatório no Notion>"
    )


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
    formato_export: str = None,  # "excel" | "pdf" | "notion" | "excel+notion" | None
    cliente_notion: str = None,  # chave do DATABASES do Notion (ex: 'source_of')
):
    """Núcleo: NLP → query banco → Claude → posta no Slack → salva histórico + log."""
    import asyncio
    t_inicio = time.time()
    resposta_final = ""
    erro_final = None
    cache_hit = False

    async def _executar():
        nonlocal resposta_final, erro_final, cache_hit
        try:
            # Verifica cache antes de tudo (para marcar cache_hit no log)
            cache_hit = cache_get(text) is not None

            append_history(thread_ts, channel, "user", text)
            # Constrói contexto uma única vez e reutiliza em consultar() e gerar_resposta()
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

            # ── Exportação de arquivo (excel / pdf) ────────────────────────────────
            fmt_arquivo = formato_export if formato_export in ("excel", "pdf") else None
            if fmt_arquivo == "excel+notion":
                fmt_arquivo = "excel"
            if fmt_arquivo:
                from reports.exporter import gerar_relatorio
                conteudo, filename, mimetype = gerar_relatorio(resultado, text, fmt_arquivo)
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
                # Se for só arquivo, retorna (Notion será tratado abaixo se solicitado)
                if formato_export not in ("notion", "excel+notion"):
                    return

            # ── Exportação para Notion ────────────────────────────────────────────
            if formato_export in ("notion", "excel+notion"):
                try:
                    from notion.relatorio import publicar_relatorio_campanha
                    await client.chat_postMessage(
                        channel=channel, thread_ts=thread_ts,
                        text="⏳ Publicando relatório no Notion...",
                        mrkdwn=True,
                    )
                    page_id, url = publicar_relatorio_campanha(
                        resultado=resultado,
                        pergunta=text,
                        cliente_key=cliente_notion,
                    )
                    resposta_final = f"Relatório publicado no Notion: {url}"
                    await client.chat_postMessage(
                        channel=channel, thread_ts=thread_ts,
                        text=(
                            f"✅ *Relatório publicado no Notion!*\n"
                            f"📎 <{url}|Abrir no Notion>"
                        ),
                        mrkdwn=True,
                    )
                except Exception as e_notion:
                    logger.exception("Erro ao publicar no Notion: %s", e_notion)
                    await client.chat_postMessage(
                        channel=channel, thread_ts=thread_ts,
                        text=f"⚠️ Não foi possível publicar no Notion: `{e_notion}`",
                        mrkdwn=True,
                    )
                return

            # Gera resposta conversacional com Claude
            # Passa o contexto já construído acima (evita segunda chamada a build_context_summary)
            resposta_final = gerar_resposta(
                pergunta=text, dados=dados, intent=intent, meta=meta,
                dados_extra=dados_extra, contexto=contexto,
            )

            append_history(thread_ts, channel, "agent", resposta_final[:500], meta)

            blocks = truncar_blocos(markdown_to_blocks(resposta_final))
            msg = await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=resposta_final[:150], blocks=blocks,
            )

            # Salva na Base de Conhecimento (não aprovado até receber 👍)
            if not cache_hit and resposta_final and not erro_final:
                try:
                    from notion.knowledge_base import salvar_entrada
                    resp_msg_ts = msg.get("ts") if msg else None
                    salvar_entrada(
                        pergunta=text,
                        resposta=resposta_final,
                        intent=intent,
                        campanha=meta.get("campanha"),
                        data_ini=meta.get("data_inicio"),
                        data_fim=meta.get("data_fim"),
                        slack_msg_ts=resp_msg_ts,
                    )
                except Exception as e_kb:
                    logger.debug("KB: erro ao salvar (não crítico): %s", e_kb)

        except Exception as e:
            erro_final = str(e)
            logger.exception("Erro ao processar mensagem: %s", e)
            await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=f"⚠️ Ocorreu um erro: `{e}`\nTente novamente.",
                mrkdwn=True,
            )

    try:
        await asyncio.wait_for(_executar(), timeout=_HANDLER_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error(
            "_processar_e_responder: timeout após %ds (user=%s, text='%s')",
            _HANDLER_TIMEOUT, user_id, text[:80],
        )
        try:
            await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=(
                    f"⏱️ *Tempo limite excedido ({_HANDLER_TIMEOUT}s).*\n"
                    "A consulta está demorando mais do que o esperado. "
                    "Tente novamente em alguns instantes ou simplifique a pergunta."
                ),
                mrkdwn=True,
            )
        except Exception:
            pass
        erro_final = f"timeout after {_HANDLER_TIMEOUT}s"
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
            from relatorio.builder import gerar_relatorio_mensal
            resultado = await loop.run_in_executor(
                None, lambda: gerar_relatorio_mensal(cliente="source_of", publicar=True)
            )
        else:
            from relatorio.builder import gerar_relatorio_semanal
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
    /pod essa semana         — semana atual
    /pod mes                 — mês atual
    /pod qual campanha...    — pergunta livre
    """
    await ack()
    channel   = command["channel_id"]
    user_id   = command["user_id"]
    arg       = command.get("text", "").strip() or "como foi essa semana?"
    thread_ts = str(__import__("time").time())

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

    except Exception as e:
        logger.debug("KB reaction handler erro (não crítico): %s", e)


# ── Comandos auxiliares ────────────────────────────────────────────────────────

@app.command("/status")
async def handle_status(ack, respond):
    """Status rápido do sistema — banco, cache, KB."""
    await ack()
    import sqlite3
    from agent.cache import _cache  # type: ignore
    from db.connection import DB_PATH

    # Banco
    try:
        conn = sqlite3.connect(DB_PATH)
        row  = conn.execute("SELECT COUNT(*), MIN(data_inicio), MAX(data_fim) FROM campanhas").fetchone()
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
