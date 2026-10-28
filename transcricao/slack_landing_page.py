"""
slack_landing_page.py

Agente de Landing Pages — fluxo conversacional 100% texto no Slack.
Sem botões. Todas as perguntas são respondidas digitando.

Fluxo:
  /criar-landing  → 1. cliente/produto
  step=cliente    → 2. objetivo
  step=objetivo   → 3. público-alvo
  step=publico    → 4. tom de voz
  step=tom        → 5. extras (ou pular)
  step=extras     → 6. referências visuais boas (ou pular)
  step=refs_boas  → 7. referências a evitar (ou pular)
  step=refs_ruins → exibe briefing + pede "confirmar"
  step=confirmar  → gera HTML via Claude
"""

import logging
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR.parent / "agente-performance" / ".env")

logger = logging.getLogger(__name__)

LP_CHANNEL = os.getenv("SLACK_IMAGE_GEN_CHANNEL_ID") or os.getenv("SLACK_IMAGE_GEN_CHANNEL", "image_gen")

# ── Estado em memória ─────────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}


def _session(user_id: str) -> dict:
    if user_id not in _sessions:
        _sessions[user_id] = {}
    return _sessions[user_id]


def _clear(user_id: str) -> None:
    _sessions.pop(user_id, None)


# ── Comando /criar-landing ────────────────────────────────────────────────────

async def cmd_criar_landing(ack, command, say):
    await ack()
    user_id   = command["user_id"]
    channel   = command["channel_id"]
    thread_ts = command.get("thread_ts")
    _clear(user_id)
    _session(user_id).update({"step": "cliente", "channel": channel, "thread_ts": thread_ts})

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        await say(**kwargs)

    await _say(text=(
        "🚀 *Criar Landing Page* — vou fazer 7 perguntas para montar o briefing.\n\n"
        "1️⃣ *Para qual cliente e produto é a landing page?*\n"
        "Ex: `Source OF — Colágeno Verisol 300g`\n"
        "Ex: `POD Kombucha — Kit Iniciante 6 sabores`"
    ))


# ── Handler de mensagens de texto ─────────────────────────────────────────────

async def handle_message_landing(user_id: str, text: str, say, channel: str,
                                  thread_ts: str | None = None) -> bool:
    """Retorna True se a mensagem foi consumida pelo fluxo de landing page."""
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

    # Cancelar em qualquer step
    if text.lower() in ("cancelar", "cancel", "sair"):
        _clear(user_id)
        await _say(text="🚫 Criação de landing page cancelada.")
        return True

    if step == "cliente":
        if not text:
            await _say(text="⚠️ Informe o cliente/produto. Tente novamente.")
            return True
        sess["cliente"] = text
        sess["step"]    = "objetivo"
        await _say(text=(
            f"✅ Cliente/Produto: *{text}*\n\n"
            "2️⃣ *Qual é o objetivo principal desta landing page?*\n"
            "Opções: `captação de leads`, `venda direta`, `lançamento de produto`, `evento/webinar`, `teste A/B`\n"
            "Ou descreva livremente."
        ))
        return True

    if step == "objetivo":
        if not text:
            await _say(text="⚠️ Informe o objetivo. Tente novamente.")
            return True
        sess["objetivo"] = text
        sess["step"]     = "publico"
        await _say(text=(
            f"✅ Objetivo: *{text}*\n\n"
            "3️⃣ *Quem é o público-alvo?*\n"
            "Ex: `Mulheres 30–50 anos, interessadas em saúde e bem-estar`\n"
            "Ex: `Homens atletas 20–35 anos, praticantes de crossfit`"
        ))
        return True

    if step == "publico":
        if not text:
            await _say(text="⚠️ Informe o público-alvo. Tente novamente.")
            return True
        sess["publico"] = text
        sess["step"]    = "tom"
        await _say(text=(
            f"✅ Público: *{text}*\n\n"
            "4️⃣ *Qual é o tom de voz?*\n"
            "Opções: `urgente e direto`, `empático e educativo`, `aspiracional/premium`, `descontraído e jovem`, `técnico/especialista`\n"
            "Ou descreva livremente."
        ))
        return True

    if step == "tom":
        if not text:
            await _say(text="⚠️ Informe o tom de voz. Tente novamente.")
            return True
        sess["tom"]  = text
        sess["step"] = "extras"
        await _say(text=(
            f"✅ Tom: *{text}*\n\n"
            "5️⃣ *Informações extras* (opcional)\n"
            "Inclua: promoção ativa, preço, benefícios específicos, depoimentos reais, garantia, diferenciais...\n"
            "Ou envie `pular` para o agente usar as melhores práticas."
        ))
        return True

    if step == "extras":
        sess["extras"] = "" if text.lower() in ("pular", "skip", "-") else text
        sess["step"]   = "refs_boas"
        await _say(text=(
            "6️⃣ *Referências visuais — o que você GOSTA* (opcional)\n"
            "Descreva em texto sites, marcas ou estilos de design que você considera referência positiva.\n"
            "Ex: `Apple.com — muito espaço em branco, tipografia grande, clean`\n"
            "Ex: `Hotmart — CTA destacado, cores fortes, direto ao ponto`\n"
            "Ex: `Minimalista, fundo branco, fotos reais do produto em destaque`\n"
            "Ou envie `pular`."
        ))
        return True

    if step == "refs_boas":
        sess["refs_boas"] = "" if text.lower() in ("pular", "skip", "-") else text
        sess["step"]      = "refs_ruins"
        await _say(text=(
            "7️⃣ *Referências visuais — o que você NÃO QUER* (opcional)\n"
            "Descreva estilos, elementos ou padrões que devem ser *evitados* no site.\n"
            "Ex: `Fundo escuro, muito texto corrido, visual dos anos 2000`\n"
            "Ex: `Sem countdown fake, sem pop-up, sem excesso de badges`\n"
            "Ex: `Não quero aquele visual gritante de info-produto barato`\n"
            "Ou envie `pular`."
        ))
        return True

    if step == "refs_ruins":
        sess["refs_ruins"] = "" if text.lower() in ("pular", "skip", "-") else text
        sess["step"]       = "confirmar"

        cliente    = sess.get("cliente", "")
        objetivo   = sess.get("objetivo", "")
        publico    = sess.get("publico", "")
        tom        = sess.get("tom", "")
        extras     = sess.get("extras", "") or "agente usará melhores práticas"
        refs_boas  = sess.get("refs_boas", "") or "não informado"
        refs_ruins = sess.get("refs_ruins", "") or "não informado"

        await _say(text=(
            "📋 *Briefing completo — revise antes de gerar:*\n\n"
            f"• *Cliente/Produto:* {cliente}\n"
            f"• *Objetivo:* {objetivo}\n"
            f"• *Público-alvo:* {publico}\n"
            f"• *Tom de voz:* {tom}\n"
            f"• *Extras:* {extras}\n"
            f"• *Referências positivas:* {refs_boas}\n"
            f"• *A evitar:* {refs_ruins}\n\n"
            "Digite *confirmar* para gerar a landing page ou *cancelar* para abortar."
        ))
        return True

    if step == "confirmar":
        if text.lower() == "confirmar":
            sess["step"] = "gerando"
            await _gerar_lp(user_id, sess, _say, channel, effective_ts)
            return True
        elif text.lower() in ("cancelar", "cancel"):
            _clear(user_id)
            await _say(text="🚫 Criação de landing page cancelada.")
            return True
        else:
            await _say(text="Digite *confirmar* para gerar ou *cancelar* para abortar.")
            return True

    return False


# ── Geração da LP ─────────────────────────────────────────────────────────────

async def _gerar_lp(user_id: str, sess: dict, say, channel: str, thread_ts: str | None = None):
    cliente    = sess.get("cliente", "")
    objetivo   = sess.get("objetivo", "")
    publico    = sess.get("publico", "")
    tom        = sess.get("tom", "")
    extras     = sess.get("extras", "")
    refs_boas  = sess.get("refs_boas", "")
    refs_ruins = sess.get("refs_ruins", "")
    _clear(user_id)

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

1. **HERO** — Headline impactante (máx 10 palavras) + Subheadline (1–2 frases) + Botão CTA principal
2. **BENEFÍCIOS** — 4 a 6 cards com emoji + título + descrição curta (2–3 linhas)
3. **COMO FUNCIONA** — 3 passos numerados e simples
4. **PROVA SOCIAL** — 3 depoimentos com nome, cargo/perfil e texto específico (não genérico)
5. **GARANTIA** — Bloco de confiança com ícone de escudo
6. **FAQ** — 4 perguntas frequentes com respostas diretas e objetivas
7. **CTA FINAL** — Headline de urgência + Botão grande + Sub-copy de reassurance
8. **RODAPÉ** — Simples: copyright + links (Política de Privacidade, Termos)

Requisitos técnicos OBRIGATÓRIOS:
- HTML5 semântico completo (<!DOCTYPE html> ... </html>)
- CSS 100% inline (style="...") — ZERO arquivos externos, ZERO <style> tags separadas
- Mobile-first: max-width 600px centralizado, padding adequado em telas pequenas
- Fonte: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif
- Esquema de cores coerente com o produto (defina uma cor primária e use consistentemente)
- Botões com border-radius, padding generoso, cor de destaque
- Seções alternando fundo branco e cinza-claro (#f8f8f8) para ritmo visual

Entregue APENAS o código HTML. Sem explicações, sem markdown, sem backticks."""

    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("AWS_CLAUDE_KEY", "")
        _anthropic = anthropic.Anthropic(api_key=api_key)
        msg = _anthropic.messages.create(
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
        _client = _WC(token=os.getenv("SLACK_BOT_TOKEN", ""))
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
        await _client.files_upload_v2(**upload_kwargs)
    except Exception as e:
        logger.exception("Erro ao enviar arquivo LP: %s", e)
        preview = html[:3000] + ("\n\n_... (truncado)_" if len(html) > 3000 else "")
        await say(text=f"✅ LP gerada (falha no upload):\n```html\n{preview}\n```")
    finally:
        try:
            fpath.unlink()
        except Exception:
            pass


# ── Tutorial ──────────────────────────────────────────────────────────────────

async def cmd_tutorial_landing(ack, respond):
    await ack()
    texto = """\
*🎓 Tutorial — Agente de Landing Pages*

Gera landing pages HTML completas pelo Slack — sem designer, sem dev.

*Como usar:*
Digite `/criar-landing` (ou mencione `@agente /criar-landing`) e responda 7 perguntas:

1. Cliente / Produto
2. Objetivo (ex: venda direta, captação de leads)
3. Público-alvo
4. Tom de voz (ex: urgente, empático, aspiracional)
5. Informações extras (ou `pular`)
6. Referências visuais que você GOSTA — sites/estilos positivos (ou `pular`)
7. Referências a EVITAR — estilos/elementos indesejados (ou `pular`)

Depois revise o briefing e digite *confirmar* para gerar.
Em qualquer momento, digite *cancelar* para abortar.

*O que é gerado:*
Arquivo `.html` mobile-first com: hero, benefícios, como funciona, depoimentos, garantia, FAQ, CTA final e rodapé.

*Para publicar:* arraste o arquivo para netlify.com/drop

*Comandos:*
`/criar-landing` — iniciar briefing
`/tutorial-landing` — este guia
_Só você está vendo esta mensagem._"""
    await respond(text=texto, response_type="ephemeral")


# ── Registro dos handlers ─────────────────────────────────────────────────────

def registrar_handlers_landing(app):
    app.command("/criar-landing")(cmd_criar_landing)
    app.command("/tutorial-landing")(cmd_tutorial_landing)
    logger.info("[landing-page] Handlers registrados no Slack app")
