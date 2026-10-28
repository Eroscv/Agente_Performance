"""
landing_page/generator.py
Geração do HTML via Claude e upload para o Slack.
"""
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from .constants import LP_CHANNEL, clear_session

logger = logging.getLogger(__name__)


async def gerar_lp(user_id: str, sess: dict, say, channel: str,
                   thread_ts: str | None = None) -> None:
    """Gera a landing page HTML via Claude e envia no Slack."""
    cliente    = sess.get("cliente", "")
    objetivo   = sess.get("objetivo", "")
    publico    = sess.get("publico", "")
    tom        = sess.get("tom", "")
    extras     = sess.get("extras", "")
    refs_boas  = sess.get("refs_boas", "")
    refs_ruins = sess.get("refs_ruins", "")
    clear_session(user_id)

    await say(text="⏳ Gerando sua landing page com Claude... isso leva ~30 segundos.")

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
        await say(text=f"❌ Erro ao gerar landing page: {e}")
        return

    slug  = re.sub(r"[^a-z0-9]", "-", cliente.lower())[:40].strip("-")
    ts    = datetime.now().strftime("%Y%m%d-%H%M")
    fname = f"landing-{slug}-{ts}.html"
    fpath = Path(tempfile.gettempdir()) / fname
    fpath.write_text(html, encoding="utf-8")

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
        await slack.files_upload_v2(**upload_kwargs)
    except Exception as e:
        logger.exception("Erro ao enviar arquivo LP: %s", e)
        preview = html[:3000] + ("\n\n_... (truncado)_" if len(html) > 3000 else "")
        await say(text=f"✅ LP gerada (falha no upload):\n```html\n{preview}\n```")
    finally:
        try:
            fpath.unlink()
        except Exception:
            pass
