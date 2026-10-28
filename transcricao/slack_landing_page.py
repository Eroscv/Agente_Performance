"""
slack_landing_page.py

Agente de Landing Pages — fluxo conversacional no Slack para
geração de landing pages completas via Claude.

Fluxo por steps:
  /criar-landing       → pergunta Cliente / Produto
  step=cliente         → pergunta Objetivo (botões)
  step=objetivo        → pergunta Público-alvo (texto)
  step=publico         → pergunta Tom de voz (botões)
  step=tom             → pergunta Informações extras (opcional)
  step=extras          → mostra briefing + botão Gerar / Cancelar
  action=lp_gerar      → gera o HTML via Claude Opus e envia como arquivo
  action=lp_cancelar   → aborta

Estado da sessão armazenado em memória por user_id.
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

# ── Opções ────────────────────────────────────────────────────────────────────

OBJETIVOS = [
    "Captação de leads",
    "Venda direta",
    "Lançamento de produto",
    "Evento / Webinar",
    "Teste A/B",
]

TONS = [
    "Urgente e direto",
    "Empático e educativo",
    "Aspiracional / Premium",
    "Descontraído e jovem",
    "Técnico / Especialista",
]

# ── Estado em memória ─────────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}


def _session(user_id: str) -> dict:
    if user_id not in _sessions:
        _sessions[user_id] = {}
    return _sessions[user_id]


def _clear(user_id: str) -> None:
    _sessions.pop(user_id, None)


# ── Helpers de bloco ──────────────────────────────────────────────────────────

def _texto(msg: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": msg}}


def _divisor() -> dict:
    return {"type": "divider"}


def _botoes(opcoes: list[str], prefix: str) -> dict:
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": op, "emoji": True},
                "value": op,
                "action_id": f"{prefix}_{re.sub(r'[^a-z0-9]', '_', op.lower())}",
            }
            for op in opcoes
        ],
    }


# ── Steps ─────────────────────────────────────────────────────────────────────

async def cmd_criar_landing(ack, command, say):
    await ack()
    user_id = command["user_id"]
    channel = command["channel_id"]
    _clear(user_id)
    _session(user_id).update({"step": "cliente", "channel": channel})

    await say(
        channel=channel,
        blocks=[
            _texto("🚀 *Criar Landing Page* — vamos montar o briefing!"),
            _divisor(),
            _texto(
                "🏢 *Para qual cliente e produto?*\n"
                "Ex: `Source OF — Suplemento Colágeno Verisol 300g`\n"
                "Ex: `POD Kombucha — Kit Iniciante 6 sabores`"
            ),
        ],
        text="Criar Landing Page — qual cliente/produto?",
    )


async def _pedir_objetivo(say, channel: str, cliente: str):
    await say(
        channel=channel,
        blocks=[
            _texto(f"✅ Cliente / Produto: *{cliente}*"),
            _divisor(),
            _texto("🎯 *Qual é o objetivo principal desta landing page?*"),
            _botoes(OBJETIVOS, "lp_obj"),
        ],
        text="Objetivo da LP?",
    )


async def _pedir_publico(say, channel: str, objetivo: str):
    await say(
        channel=channel,
        blocks=[
            _texto(f"✅ Objetivo: *{objetivo}*"),
            _divisor(),
            _texto(
                "👥 *Qual é o público-alvo?*\n"
                "Ex: `Mulheres 30–50 anos, interessadas em saúde e bem-estar`\n"
                "Ex: `Homens atletas 20–35 anos, praticantes de crossfit`"
            ),
        ],
        text="Público-alvo?",
    )


async def _pedir_tom(say, channel: str, publico: str):
    await say(
        channel=channel,
        blocks=[
            _texto(f"✅ Público: *{publico}*"),
            _divisor(),
            _texto("🎙️ *Tom de voz da landing page?*"),
            _botoes(TONS, "lp_tom"),
        ],
        text="Tom de voz?",
    )


async def _pedir_extras(say, channel: str, tom: str):
    await say(
        channel=channel,
        blocks=[
            _texto(f"✅ Tom: *{tom}*"),
            _divisor(),
            _texto(
                "📎 *Informações extras* _(opcional)_\n"
                "Inclua: promoção ativa, preço, benefícios específicos, selos, "
                "depoimentos reais, urgência, garantia, diferenciais...\n"
                "_Envie `pular` para o agente usar as melhores práticas para o produto._"
            ),
        ],
        text="Informações extras?",
    )


async def _mostrar_briefing(say, channel: str, sess: dict):
    cliente  = sess.get("cliente", "")
    objetivo = sess.get("objetivo", "")
    publico  = sess.get("publico", "")
    tom      = sess.get("tom", "")
    extras   = sess.get("extras", "") or "_sem extras — agente usará melhores práticas_"

    await say(
        channel=channel,
        blocks=[
            _texto("📋 *Briefing — confirmar geração da Landing Page?*"),
            _divisor(),
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Cliente / Produto:*\n{cliente}"},
                    {"type": "mrkdwn", "text": f"*Objetivo:*\n{objetivo}"},
                    {"type": "mrkdwn", "text": f"*Público-alvo:*\n{publico}"},
                    {"type": "mrkdwn", "text": f"*Tom de voz:*\n{tom}"},
                    {"type": "mrkdwn", "text": f"*Extras:*\n{extras}"},
                ],
            },
            _divisor(),
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "🚀 Gerar Landing Page", "emoji": True},
                        "style": "primary",
                        "value": "gerar",
                        "action_id": "lp_gerar",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Cancelar", "emoji": True},
                        "style": "danger",
                        "value": "cancelar",
                        "action_id": "lp_cancelar",
                    },
                ],
            },
        ],
        text="Confirmar briefing e gerar LP?",
    )


# ── Handler de texto ──────────────────────────────────────────────────────────

async def handle_message_landing(user_id: str, text: str, say, channel: str) -> bool:
    """Retorna True se a mensagem foi consumida pelo fluxo de landing page."""
    sess = _sessions.get(user_id)
    if not sess:
        return False

    step = sess.get("step")
    text = text.strip()

    if step == "cliente":
        if not text:
            await say(channel=channel, text="⚠️ Informe o cliente/produto. Tente novamente.")
            return True
        sess["cliente"] = text
        sess["step"]    = "objetivo"
        await _pedir_objetivo(say, channel, text)
        return True

    if step == "publico":
        sess["publico"] = text
        sess["step"]    = "tom"
        await _pedir_tom(say, channel, text)
        return True

    if step == "extras":
        sess["extras"] = "" if text.lower() in ("pular", "skip", "-") else text
        sess["step"]   = "confirmar"
        await _mostrar_briefing(say, channel, sess)
        return True

    return False


# ── Handlers de botões ────────────────────────────────────────────────────────

async def handle_action_objetivo(ack, body, say):
    await ack()
    user_id  = body["user"]["id"]
    sess     = _session(user_id)
    objetivo = body["actions"][0]["value"]
    channel  = body["container"]["channel_id"]
    sess["objetivo"] = objetivo
    sess["step"]     = "publico"
    await _pedir_publico(say, channel, objetivo)


async def handle_action_tom(ack, body, say):
    await ack()
    user_id = body["user"]["id"]
    sess    = _session(user_id)
    tom     = body["actions"][0]["value"]
    channel = body["container"]["channel_id"]
    sess["tom"]  = tom
    sess["step"] = "extras"
    await _pedir_extras(say, channel, tom)


async def handle_action_gerar(ack, body, say, client):
    """Gera a landing page completa via Claude Opus e envia como arquivo .html."""
    await ack()
    user_id = body["user"]["id"]
    sess    = _sessions.get(user_id, {})
    channel = body["container"]["channel_id"]

    if not sess:
        await say(channel=channel, text="⚠️ Sessão expirada. Use `/criar-landing` para recomeçar.")
        return

    cliente  = sess.get("cliente", "")
    objetivo = sess.get("objetivo", "")
    publico  = sess.get("publico", "")
    tom      = sess.get("tom", "")
    extras   = sess.get("extras", "")

    _clear(user_id)

    await say(channel=channel, text="⏳ Gerando sua landing page com Claude... isso leva ~30 segundos.")

    prompt = f"""Você é um copywriter especialista em landing pages de alta conversão.

Crie uma landing page COMPLETA em HTML com CSS inline (mobile-first, sem dependências externas) para:

**Cliente / Produto:** {cliente}
**Objetivo:** {objetivo}
**Público-alvo:** {publico}
**Tom de voz:** {tom}
**Informações extras:** {extras if extras else "Use as melhores práticas para o produto mencionado."}

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
- Responsivo: use max-width e margin auto nos containers

Entregue APENAS o código HTML. Sem explicações, sem markdown, sem ```html fences."""

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
        # Remove fences se Claude adicionou mesmo assim
        html = re.sub(r"^```[a-z]*\n?", "", html)
        html = re.sub(r"\n?```$", "", html)
        html = html.strip()
    except Exception as e:
        logger.exception("Erro ao gerar LP via Claude: %s", e)
        await say(channel=channel, text=f"❌ Erro ao gerar landing page: {e}")
        return

    # Salva como arquivo temporário e sobe no Slack
    slug  = re.sub(r"[^a-z0-9]", "-", cliente.lower())[:40].strip("-")
    ts    = datetime.now().strftime("%Y%m%d-%H%M")
    fname = f"landing-{slug}-{ts}.html"
    fpath = Path(tempfile.gettempdir()) / fname
    fpath.write_text(html, encoding="utf-8")

    try:
        await client.files_upload_v2(
            channel=channel,
            file=str(fpath),
            filename=fname,
            title=f"Landing Page — {cliente}",
            initial_comment=(
                f"✅ *Landing Page gerada com sucesso!*\n"
                f"📌 *{cliente}*  |  🎯 {objetivo}  |  👥 {publico}\n"
                f"_Baixe o arquivo `.html` e abra no browser para visualizar. "
                f"Para publicar: arraste para o Netlify Drop ou suba no GitHub Pages._"
            ),
        )
    except Exception as e:
        logger.exception("Erro ao enviar arquivo LP: %s", e)
        # Fallback: posta preview
        preview = html[:3000] + ("\n\n_... (truncado)_" if len(html) > 3000 else "")
        await say(channel=channel, text=f"✅ LP gerada (falha no upload):\n```html\n{preview}\n```")
    finally:
        try:
            fpath.unlink()
        except Exception:
            pass


async def handle_action_cancelar(ack, body, say):
    await ack()
    user_id = body["user"]["id"]
    channel = body["container"]["channel_id"]
    _clear(user_id)
    await say(channel=channel, text="🚫 Criação de landing page cancelada.")


# ── Tutorial ──────────────────────────────────────────────────────────────────

async def cmd_tutorial_landing(ack, respond):
    """Tutorial do Agente de Landing Pages."""
    await ack()
    texto = """\
*🎓 Tutorial — Agente de Landing Pages*
_Gera landing pages completas em HTML prontas para usar, diretamente pelo Slack._

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*O que é este agente?*

Você preenche um briefing em menos de 2 minutos pelo Slack e recebe uma landing page HTML completa, mobile-first e pronta para subir no ar — sem precisar de designer ou dev.

Fluxo interno:
`Briefing (Slack)` → `Claude Opus (copywriter)` → `HTML completo` → `📎 arquivo no canal`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*🚀 Como criar uma landing page*

Use `/criar-landing` e responda 5 perguntas:

1️⃣ *Cliente / Produto*
`Source OF — Colágeno Verisol 300g`

2️⃣ *Objetivo* (botões)
Captação de leads · Venda direta · Lançamento de produto · Evento/Webinar · Teste A/B

3️⃣ *Público-alvo* (texto)
`Mulheres 30–50 anos, foco em saúde e bem-estar`

4️⃣ *Tom de voz* (botões)
Urgente e direto · Empático e educativo · Aspiracional/Premium · Descontraído e jovem · Técnico/Especialista

5️⃣ *Informações extras* (texto ou `pular`)
Promoção, preço, depoimentos reais, garantia, diferenciais...

Clique em *🚀 Gerar Landing Page* — pronto!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📄 O que é gerado*

Um arquivo `.html` completo, mobile-first, com:
🎯 *Hero* — headline + subheadline + CTA principal
✅ *Benefícios* — 4–6 cards com emoji e descrições
📋 *Como funciona* — 3 passos simples
⭐ *Prova social* — 3 depoimentos específicos
🛡️ *Garantia* — bloco de confiança
❓ *FAQ* — 4 perguntas frequentes
🔥 *CTA final* — urgência + botão de conversão
🔗 *Rodapé* — copyright + links legais

Tudo com CSS inline — abre direto no browser, sem dependências externas.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*💡 Exemplos de uso*

`/criar-landing` → POD Kombucha · Venda direta · Público fitness 20–35 · Descontraído
`/criar-landing` → Botica Ouro Preto · Lançamento · Amantes de cosméticos naturais · Aspiracional
`/criar-landing` → Source OF Colágeno · Captação leads · Mulheres 35–55 · Empático

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*⚠️ Limites e dicas*

• Geração leva *~30 segundos* (Claude Opus processando)
• Resultado: arquivo `.html` — baixe e abra no browser
• Para publicar: arraste para *netlify.com/drop* ou suba no GitHub Pages
• Quanto mais detalhes nos *extras*, mais personalizada a LP

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*⚡ Slash Commands*
`/criar-landing` — inicia o briefing
`/tutorial-landing` — este guia

*Outros agentes:*
`/tutorial` — Agente de Performance (métricas)
`/tutorial-transcricao` — Agente de Transcrição (atas → Notion)
_Só você está vendo esta mensagem._
"""
    await respond(text=texto, response_type="ephemeral")


# ── Registro dos handlers ─────────────────────────────────────────────────────

def registrar_handlers_landing(app):
    """
    Registra os handlers do Agente de Landing Pages no AsyncApp.
    Chame em handlers.py junto com os demais registros.
    """
    app.command("/criar-landing")(cmd_criar_landing)
    app.command("/tutorial-landing")(cmd_tutorial_landing)

    app.action({"action_id": re.compile(r"^lp_obj_")})(handle_action_objetivo)
    app.action({"action_id": re.compile(r"^lp_tom_")})(handle_action_tom)
    app.action("lp_gerar")(handle_action_gerar)
    app.action("lp_cancelar")(handle_action_cancelar)

    logger.info("[landing-page] Handlers registrados no Slack app")
