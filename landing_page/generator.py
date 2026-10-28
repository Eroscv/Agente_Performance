"""
landing_page/generator.py
Geração do HTML via Claude e upload para o Slack.
Salva projeto, preferências e referências no banco após gerar.
"""
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from .constants import LP_CHANNEL, clear_session
from .db.queries import (
    salvar_projeto, upsert_preferencias,
    salvar_ref, encerrar_sessao,
    salvar_mensagem, salvar_html, atualizar_status_projeto,
)

logger = logging.getLogger(__name__)


async def gerar_lp(user_id: str, sess: dict, say, channel: str,
                   thread_ts: str | None = None) -> None:
    """Gera a landing page HTML via Claude, salva no banco e envia no Slack."""
    cliente    = sess.get("cliente", "")
    objetivo   = sess.get("objetivo", "")
    publico    = sess.get("publico", "")
    tom        = sess.get("tom", "")
    extras     = sess.get("extras", "")
    refs_boas  = sess.get("refs_boas", "")
    refs_ruins = sess.get("refs_ruins", "")
    sid        = sess.get("sessao_id", "")

    clear_session(user_id)

    await say(text="⏳ Gerando sua landing page com Claude... isso leva ~30 segundos.")
    if sid:
        salvar_mensagem(user_id, sid, "agent", "⏳ Gerando landing page...")

    refs_section = ""
    if refs_boas:
        refs_section += f"\n**Referências visuais positivas (inspire-se nestes estilos):** {refs_boas}"
    if refs_ruins:
        refs_section += f"\n**Referências a EVITAR (não use estes estilos/elementos):** {refs_ruins}"

    prompt = f"""Você é um copywriter especialista em landing pages de alta conversão.

Crie uma landing page COMPLETA em HTML com CSS inline (mobile-first, sem dependências externas) para:

**Cliente / Produto:** {cliente}
**Objetivo:** {objetivo}
**Público-alvo:** {publico}
**Tom de voz:** {tom}
**Informações extras:** {extras if extras else "Use as melhores práticas para o produto mencionado."}{refs_section}

Estrutura OBRIGATÓRIA (nesta ordem):
1. HERO — Headline (máx 10 palavras) + Subheadline + CTA principal
2. BENEFÍCIOS — 4–6 cards com emoji + título + descrição
3. COMO FUNCIONA — 3 passos numerados
4. PROVA SOCIAL — 3 depoimentos específicos (nome, cargo, texto)
5. GARANTIA — Bloco de confiança com escudo
6. FAQ — 4 perguntas e respostas diretas
7. CTA FINAL — Headline de urgência + botão + sub-copy
8. RODAPÉ — copyright + links legais

Requisitos técnicos OBRIGATÓRIOS:
- HTML5 semântico completo (<!DOCTYPE html> ... </html>)
- CSS 100% inline (style="...") — ZERO arquivos externos
- Mobile-first: max-width 600px centralizado
- Fonte: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif
- Cores coerentes com o produto
- Seções alternando fundo branco e #f8f8f8

Entregue APENAS o código HTML. Sem explicações, sem markdown, sem backticks."""

    html = None
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("AWS_CLAUDE_KEY", "")
        client  = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=os.getenv("CLAUDE_MODEL_LP", "claude-opus-4-5"),
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        html = msg.content[0].text.strip()
        html = re.sub(r"^```[a-z]*\n?", "", html)
        html = re.sub(r"\n?```$", "", html)
        html = html.strip()
    except Exception as e:
        logger.exception("Erro ao gerar LP via Claude: %s", e)
        # Salva projeto com status falha
        salvar_projeto(
            user_id=user_id, cliente=cliente, objetivo=objetivo,
            publico=publico, tom=tom, extras=extras,
            refs_boas=refs_boas, refs_ruins=refs_ruins,
            status="falha", observacao=str(e),
        )
        if sid:
            encerrar_sessao(sid, "gerando", "cancelado")
        await say(text=f"❌ Erro ao gerar landing page: {e}")
        return

    # Monta nome do arquivo
    slug  = re.sub(r"[^a-z0-9]", "-", cliente.lower())[:40].strip("-")
    ts    = datetime.now().strftime("%Y%m%d-%H%M")
    fname = f"landing-{slug}-{ts}.html"
    fpath = Path(tempfile.gettempdir()) / fname
    fpath.write_text(html, encoding="utf-8")

    slack_ts_upload = ""
    try:
        from slack_sdk.web.async_client import AsyncWebClient as _WC
        slack = _WC(token=os.getenv("SLACK_BOT_TOKEN", ""))
        upload_kwargs = dict(
            channel=channel,
            file=str(fpath),
            filename=fname,
            title=f"Landing Page — {cliente}",
            initial_comment=(
                f"✅ *Landing Page gerada!*\n"
                f"📌 *{cliente}*  |  🎯 {objetivo}  |  👥 {publico}\n"
                f"_Baixe o `.html`, abra no browser. Para publicar: arraste para netlify.com/drop_"
            ),
        )
        if thread_ts:
            upload_kwargs["thread_ts"] = thread_ts
        result = await slack.files_upload_v2(**upload_kwargs)
        slack_ts_upload = str(result.get("ts", ""))
    except Exception as e:
        logger.exception("Erro ao enviar arquivo LP: %s", e)
        preview = html[:3000] + ("\n\n_... (truncado)_" if len(html) > 3000 else "")
        await say(text=f"✅ LP gerada (falha no upload):\n```html\n{preview}\n```")
    finally:
        try:
            fpath.unlink()
        except Exception:
            pass

    # ── Persistência no banco ─────────────────────────────────────────────────
    # Se veio de geração assíncrona, atualiza o projeto pré-criado; senão cria novo
    projeto_id_pre = sess.get("_projeto_id_pre") if sess else None
    if projeto_id_pre:
        atualizar_status_projeto(projeto_id_pre, "gerado")
        conn_exec = __import__("landing_page.db.database", fromlist=["conn"]).conn()
        conn_exec.execute(
            "UPDATE projetos SET arquivo_nome=?, slack_channel=?, slack_ts=? WHERE id=?",
            (fname, channel, slack_ts_upload, projeto_id_pre),
        )
        conn_exec.commit()
        projeto_id = projeto_id_pre
    else:
        projeto_id = salvar_projeto(
            user_id=user_id, cliente=cliente, objetivo=objetivo,
            publico=publico, tom=tom, extras=extras,
            refs_boas=refs_boas, refs_ruins=refs_ruins,
            arquivo_nome=fname, slack_channel=channel,
            slack_ts=slack_ts_upload, status="gerado",
        )

    # Salva HTML completo no banco para recuperação futura
    salvar_html(projeto_id, html)

    # Salva referências acumulativas
    if refs_boas:
        salvar_ref(user_id, "boa",  refs_boas,  projeto_id)
    if refs_ruins:
        salvar_ref(user_id, "ruim", refs_ruins, projeto_id)

    # Atualiza preferências do usuário
    upsert_preferencias(
        user_id,
        tom_favorito=tom,
        cliente_padrao=cliente,
    )

    # Encerra sessão
    if sid:
        encerrar_sessao(sid, "gerado", "gerado")
        salvar_mensagem(user_id, sid, "agent", f"✅ LP gerada: {fname}")

    logger.info("[LP] Projeto #%d salvo — %s para %s", projeto_id, fname, user_id)
