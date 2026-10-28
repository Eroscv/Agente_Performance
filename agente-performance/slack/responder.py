"""
slack/responder.py

Núcleo de processamento de mensagens Slack:
  _processar_e_responder() — NLP → query banco → Claude → posta no Slack
"""
import asyncio
import logging
import os
import time
from typing import Optional

from agent.engine import consultar
from agent.claude_client import gerar_resposta
from agent.context_store import append_history, build_context_summary
from agent.cache import get as cache_get
from agent.conversation_log import registrar as log_registrar
from db.consultas_log import registrar_consulta, aprovar_consulta as _aprovar_consulta_log
from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n, roas_emoji
from slack.helpers import _detectar_formato_export, _sugestao_proativa
from slack.formatter import markdown_to_blocks, truncar_blocos

logger = logging.getLogger(__name__)

# ── Detecção e processamento de imagem ───────────────────────────────────────
# Funções de detecção delegadas ao image_agent (fonte única de verdade)
import re as _re
from agent.image_agent import (
    e_pedido_imagem as _e_pedido_imagem,
    e_pedido_variacao as _e_pedido_variacao,
    e_pedido_edicao_imagem as _e_pedido_edicao,
    e_pedido_analise_imagem as _e_pedido_analise,
)


async def _processar_imagem(
    text:      str,
    channel:   str,
    thread_ts: str,
    client,
    user_id:   str = "",
) -> None:
    """
    Pipeline de geração de imagem:
    1. Verifica rate limit do usuário
    2. Extrai prompt limpo (remove verbos de comando)
    3. Detecta se é pedido de variação da última imagem
    4. Chama image_agent.gerar_imagem() em executor (não bloqueia)
    5. Upload sempre em #image_gen
    6. Confirma no canal de origem
    """
    from agent.image_agent import gerar_imagem, verificar_rate_limit

    IMAGE_GEN_CHANNEL = os.getenv("SLACK_IMAGE_GEN_CHANNEL", "image_gen")

    # ── Rate limit ──
    pode_gerar, contagem = verificar_rate_limit(user_id)
    if not pode_gerar:
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=(
                f":no_entry: Limite atingido: você já gerou *{contagem} imagens* na última hora.\n"
                f"> Máximo: {os.getenv('IMAGE_RATE_LIMIT_MAX', '10')} por hora. Tente novamente mais tarde."
            ),
            mrkdwn=True,
        )
        return

    # ── Extrai prompt ──
    prompt = _re.sub(
        r"^(ger(?:a|e|ar?|ou)|cr(?:ia|ie|iar?)|fa(?:z|ça|zer?)|gere?|crie?|faça?|draw|create?|generat\w*|produz\w*)\s+"
        r"(?:uma?\s+)?(?:imagem|foto|banner|criativo|arte|visual|thumbnail|ilustração|story|stories|reels|capa)?\s*",
        "",
        text,
        flags=_re.IGNORECASE,
    ).strip() or text

    # ── Variação: adiciona contexto da última imagem gerada na thread ──
    if _e_pedido_variacao(text):
        try:
            import sqlite3
            from pathlib import Path
            db = Path(__file__).resolve().parent.parent / "agente.db"
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT prompt_refinado FROM image_log WHERE channel=? AND status='ok' ORDER BY criado_em DESC LIMIT 1",
                (channel,)
            ).fetchone()
            conn.close()
            if row and row[0]:
                prompt = f"{row[0]}, but {prompt}"  # contexto da geração anterior
                logger.info("[image] Variação detectada — base: %s", row[0][:60])
        except Exception as e_var:
            logger.debug("[image] Falha ao buscar variação: %s", e_var)

    logger.info("[image] Pedido | user=%s | prompt=%s", user_id, prompt[:80])

    # Feedback imediato
    await client.chat_postMessage(
        channel=channel, thread_ts=thread_ts,
        text=f":art: Gerando imagem\u2026 será enviada em <#{IMAGE_GEN_CHANNEL}>.",
    )

    try:
        resultado = await asyncio.get_event_loop().run_in_executor(
            None, lambda: gerar_imagem(
                prompt,
                user_id=user_id,
                channel=channel,
                texto_slack=text,
            )
        )

        # Upload em #image_gen
        await client.files_upload_v2(
            channel=IMAGE_GEN_CHANNEL,
            content=resultado.bytes_png,
            filename=resultado.filename,
            title=f"\U0001f5bc {prompt[:60]}",
        )

        # Mensagem de resultado em #image_gen
        await client.chat_postMessage(
            channel=IMAGE_GEN_CHANNEL,
            text=(
                f":white_check_mark: *{prompt[:80]}*\n"
                f"> :pencil: _Prompt:_ {resultado.prompt_refinado[:120]}\n"
                f"> :frame_with_picture: {resultado.formato_label} · {resultado.modelo} · {resultado.size_kb} KB"
            ),
            mrkdwn=True,
        )

        # Confirma no canal de origem
        if channel != IMAGE_GEN_CHANNEL:
            await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=f":white_check_mark: Imagem *{resultado.formato_label}* gerada em <#{IMAGE_GEN_CHANNEL}>!",
                mrkdwn=True,
            )

        logger.info("[image] OK | modelo=%s fmt=%s size_kb=%d",
                    resultado.modelo, resultado.formato_label, resultado.size_kb)

    except RuntimeError as e:
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f":x: Não consegui gerar a imagem.\n> {e}",
            mrkdwn=True,
        )
    except Exception as e:
        logger.exception("[image] Erro inesperado")
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f":x: Erro ao gerar imagem: {e}",
        )


async def _baixar_imagem_slack(file_info: dict, client) -> tuple[bytes, str]:
    """
    Baixa uma imagem do Slack usando url_private.
    Retorna (bytes, mime_type).
    """
    import aiohttp
    url       = file_info.get("url_private") or file_info.get("url_private_download", "")
    mime_type = file_info.get("mimetype", "image/png")
    token     = os.getenv("SLACK_BOT_TOKEN", "")

    # Tenta aiohttp primeiro, fallback para requests síncrono
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                resp.raise_for_status()
                return await resp.read(), mime_type
    except Exception:
        import requests
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        return r.content, mime_type


async def _processar_imagem_recebida(
    text:      str,
    files:     list,
    channel:   str,
    thread_ts: str,
    client,
    user_id:   str = "",
) -> None:
    """
    Fluxo quando usuário envia imagem + texto:
    - Se pedir análise/descrição  → Gemini vision (só texto)
    - Se pedir edição/alteração   → Gemini vision+gen → #image_gen
    - Se só imagem sem texto       → descreve automaticamente
    """
    from agent.image_agent import analisar_imagem, editar_imagem, verificar_rate_limit

    IMAGE_GEN_CHANNEL = os.getenv("SLACK_IMAGE_GEN_CHANNEL", "image_gen")

    # Filtra apenas imagens (ignora PDFs, etc.)
    imagens = [
        f for f in files
        if f.get("mimetype", "").startswith("image/")
    ]
    if not imagens:
        return  # nenhuma imagem real, ignora

    file_info = imagens[0]  # usa a primeira imagem
    texto     = text.strip()
    eh_edicao  = _e_pedido_edicao(texto)
    eh_analise = _e_pedido_analise(texto) or not texto  # sem texto = descreve

    # Feedback imediato
    if eh_edicao:
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=":art: Processando sua imagem\u2026 resultado em <#{IMAGE_GEN_CHANNEL}>.",
        )
    else:
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=":eyes: Analisando a imagem\u2026",
        )

    try:
        img_bytes, mime_type = await _baixar_imagem_slack(file_info, client)
    except Exception as e:
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f":x: Não consegui acessar a imagem: {e}",
        )
        return

    # ── EDITAR ──
    if eh_edicao:
        pode_gerar, contagem = verificar_rate_limit(user_id)
        if not pode_gerar:
            await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=f":no_entry: Limite atingido ({contagem} imagens/hora). Tente mais tarde.",
            )
            return
        try:
            resultado = await asyncio.get_event_loop().run_in_executor(
                None, lambda: editar_imagem(
                    img_bytes, texto,
                    user_id=user_id, channel=channel,
                    texto_slack=text, mime_type=mime_type,
                )
            )
            await client.files_upload_v2(
                channel=IMAGE_GEN_CHANNEL,
                content=resultado.bytes_png,
                filename=resultado.filename,
                title=f"\U0001f5bc Edit: {texto[:50]}",
            )
            await client.chat_postMessage(
                channel=IMAGE_GEN_CHANNEL,
                text=(
                    f":pencil2: *Edição:* {texto[:80]}\n"
                    f"> :frame_with_picture: {resultado.formato_label} · {resultado.modelo} · {resultado.size_kb} KB"
                ),
                mrkdwn=True,
            )
            if channel != IMAGE_GEN_CHANNEL:
                await client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text=f":white_check_mark: Imagem editada enviada em <#{IMAGE_GEN_CHANNEL}>!",
                )
        except Exception as e:
            logger.exception("[image:edit] erro")
            await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=f":x: Não consegui editar: {e}",
            )
        return

    # ── ANALISAR ──
    try:
        descricao = await asyncio.get_event_loop().run_in_executor(
            None, lambda: analisar_imagem(img_bytes, texto, mime_type)
        )
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f":mag: {descricao}",
        )
    except Exception as e:
        logger.exception("[image:analyze] erro")
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=f":x: Não consegui analisar a imagem: {e}",
        )


_HANDLER_TIMEOUT = int(os.getenv("SLACK_HANDLER_TIMEOUT", "60"))

async def _processar_e_responder(
    text:      str,
    channel:   str,
    thread_ts: str,
    client,
    user_id:   str = None,
    formato_export: str = None,
    cliente_notion: str = None,
    files:     list = None,   # arquivos anexados ao evento Slack
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
            append_history(thread_ts, channel, "user", text)
            # Constrói contexto uma única vez e reutiliza em consultar() e gerar_resposta()
            contexto = build_context_summary(thread_ts)

            # ── Intercept: imagem recebida (arquivos anexados) ─────────────────
            if files:
                await _processar_imagem_recebida(
                    text, files, channel, thread_ts, client, user_id=user_id or ""
                )
                resposta_final = "[imagem recebida]"
                return
            # ─────────────────────────────────────────────────────────────

            # ── Intercept: geração de imagem ─────────────────────────────────
            if _e_pedido_imagem(text):
                await _processar_imagem(text, channel, thread_ts, client, user_id=user_id or "")
                resposta_final = "[imagem gerada]"
                return
            # ─────────────────────────────────────────────────────────────

            resultado   = consultar(text, thread_ts=thread_ts, channel=channel, user_id=user_id)
            dados       = resultado["dados"]
            intent      = resultado["intent"]
            meta        = resultado["meta"]
            dados_extra = resultado.get("dados_extra", {})
            erro        = resultado.get("erro")
            aviso_vazio = resultado.get("aviso_vazio")
            aviso_delay = meta.get("aviso_delay")

            # cache_hit baseado no cliente correto (POD usa prefixo "pod::")
            is_pod_result = meta.get("cliente") == "pod_kombucha"
            _ck = f"pod::{text}" if is_pod_result else text
            cache_hit = cache_get(_ck) is not None

            # Usa contexto do resultado POD (quando disponível) em vez do da thread Source OF
            if resultado.get("contexto"):
                contexto = resultado["contexto"]

            # Campanha não encontrada ou intent desconhecida — responde diretamente sem chamar Claude
            if erro:
                # MELHORIA 2: Fallback inteligente com sugestão de comando
                # Se o intent for desconhecido, enriquece a mensagem com sugestão
                if resultado.get("intent") == "desconhecida":
                    try:
                        from agent.nlp import sugerir_intent_por_keywords
                        sugestao = sugerir_intent_por_keywords(text)
                        if sugestao:
                            intent_sug, cmd_sug = sugestao
                            erro = (
                                f"🤔 Não entendi bem o que você quis dizer.\n\n"
                                f"Você quis dizer *{cmd_sug}*?\n\n"
                                f"Ou tente:\n"
                                f"• `/ajuda` — ver todos os comandos\n"
                                f"• Reformule a pergunta com mais detalhes"
                            )
                        else:
                            erro = (
                                f"🤔 Não entendi bem o que você quis dizer.\n\n"
                                f"Tente:\n"
                                f"• `/ajuda` — ver todos os comandos\n"
                                f"• Exemplos: *como foi o ROAS essa semana?*, *top 5 campanhas*, *campanhas sem conversão*"
                            )
                    except Exception as _e2:
                        logger.debug("[handler] sugerir_intent_por_keywords: %s", _e2)

                resposta_final = erro
                erro_final = erro
                await client.chat_postMessage(
                    channel=channel, thread_ts=thread_ts,
                    text=erro, mrkdwn=True,
                )
                return

            # MELHORIA 8: Alerta de cobertura de dados (prefixo na resposta)
            aviso_cobertura = None
            try:
                aviso_cobertura = _verificar_cobertura_dados_handler(
                    data_inicio=meta.get("data_inicio"),
                    data_fim=meta.get("data_fim"),
                    cliente_key=meta.get("cliente"),
                    plataforma=meta.get("plataforma"),
                )
            except Exception as _e8:
                logger.debug("[handler] cobertura_dados: %s", _e8)

            # MELHORIA 10: Fallback quando sync falhou para período recente vazio
            if not dados or (isinstance(dados, list) and len(dados) == 0):
                try:
                    from datetime import date as _date, timedelta as _td
                    _meta_fim = meta.get("data_fim")
                    _hoje = _date.today()
                    if _meta_fim and (_hoje - _date.fromisoformat(_meta_fim)).days <= 3:
                        aviso_sync = _verificar_sync_falhou(
                            cliente_key=meta.get("cliente"),
                            plataforma=meta.get("plataforma"),
                        )
                        if aviso_sync and not aviso_vazio:
                            aviso_vazio = aviso_sync
                except Exception as _e10:
                    logger.debug("[handler] verificar_sync_falhou: %s", _e10)

            # Dados vazios com sugestão de período
            # Para POD: aviso_vazio pode ser apenas nota de período ajustado (não interrompe a resposta)
            aviso_periodo_pod = None
            if aviso_vazio:
                if is_pod_result and aviso_vazio.startswith("⚠️"):
                    # Aviso de período ajustado: adiciona como nota, não substitui a resposta
                    aviso_periodo_pod = aviso_vazio
                    aviso_vazio = None  # não interrompe o fluxo
                else:
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
                delta=resultado.get("delta", {}),
                shopify=resultado.get("shopify", {}),  # receita real Shopify
            )

            # Prefixo de aviso de período ajustado (POD, item 13)
            if aviso_periodo_pod:
                resposta_final = aviso_periodo_pod + "\n\n" + resposta_final

            # Aviso de delay de dados (hoje pedido, banco só tem até ontem)
            if aviso_delay:
                resposta_final = aviso_delay + "\n\n" + resposta_final

            # Aviso de janela de atribuição (dados recentes ainda podem mudar)
            aviso_janela = meta.get("aviso_janela_atribuicao")
            if aviso_janela:
                resposta_final = resposta_final + "\n\n" + aviso_janela

            # MELHORIA 8: Prefixo de alerta de cobertura de dados
            if aviso_cobertura:
                resposta_final = aviso_cobertura + "\n\n" + resposta_final

            # ── Fact-check + Confidence + Evidence stamp ──────────────────────
            try:
                from agent.data_verifier import verificar_resposta as _verificar
                _dv = _verificar(
                    resposta_final,
                    dados,
                    meta,
                    dados_brutos_extras={
                        "delta":   resultado.get("delta", {}),
                        "shopify": resultado.get("shopify", {}),
                    },
                )
                if _dv.prefixo_aviso:
                    resposta_final = _dv.prefixo_aviso + resposta_final
                if _dv.evidence_stamp:
                    resposta_final = resposta_final + _dv.evidence_stamp
            except Exception as _dv_err:
                logger.debug("[responder] data_verifier falhou (não crítico): %s", _dv_err)

            append_history(thread_ts, channel, "agent", resposta_final[:500], meta)

            blocks = truncar_blocos(markdown_to_blocks(resposta_final))
            msg = await client.chat_postMessage(
                channel=channel, thread_ts=thread_ts,
                text=resposta_final[:150], blocks=blocks,
            )

            # Salva na Base de Conhecimento (não aprovado até receber 👍)
            if not cache_hit and resposta_final and not erro_final:
                resp_msg_ts = msg.get("ts") if msg else None
                try:
                    from notion.knowledge_base import salvar_entrada
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
                    logger.debug("KB: erro ao salvar no Notion (não crítico): %s", e_kb)
                # MELHORIA 9: Salvar também na tabela local kb_cache (campanhas.db)
                try:
                    _kb_local_salvar(
                        pergunta=text,
                        resposta=resposta_final,
                        intent=intent,
                        msg_ts=resp_msg_ts,
                    )
                except Exception as e_kb_local:
                    logger.debug("KB local: erro ao salvar (não crítico): %s", e_kb_local)

            # MELHORIA 7: Sugestões proativas após resposta principal
            if resposta_final and not erro_final and not cache_hit:
                try:
                    sugestao = _sugestao_proativa(intent)
                    if sugestao:
                        await client.chat_postMessage(
                            channel=channel,
                            thread_ts=thread_ts,
                            text=sugestao,
                            mrkdwn=True,
                        )
                except Exception as e_sug:
                    logger.debug("[handler] sugestão proativa: %s", e_sug)

            # Salvar consulta no banco (consultas_usuarios)
            try:
                duracao_ms = int((time.time() - t_inicio) * 1000)
                resp_msg_ts_log = msg.get("ts") if msg else None
                registrar_consulta(
                    pergunta=text,
                    resposta=resposta_final or None,
                    intent=intent if 'intent' in dir() else None,
                    cliente_key=meta.get("cliente") if 'meta' in dir() else None,
                    plataforma=meta.get("plataforma") if 'meta' in dir() else None,
                    data_inicio=str(meta.get("data_inicio")) if 'meta' in dir() and meta.get("data_inicio") else None,
                    data_fim=str(meta.get("data_fim")) if 'meta' in dir() and meta.get("data_fim") else None,
                    user_id=user_id,
                    channel=channel,
                    thread_ts=thread_ts,
                    slack_msg_ts=resp_msg_ts_log,
                    cache_hit=cache_hit,
                    tem_erro=bool(erro_final),
                    duracao_ms=duracao_ms,
                )
            except Exception as e_clog:
                logger.debug("[ConsultasLog] erro ao salvar (não crítico): %s", e_clog)

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
