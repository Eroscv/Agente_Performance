"""
slack_criar_card.py

Fluxo conversacional no Slack para criação manual de cards no Notion
(database Agenda e Registros).

Fluxo por steps:
  /criar-card         → pergunta Título
  step=titulo         → pergunta Responsável (lista com botões)
  step=responsavel    → pergunta Revisor (lista com botões)
  step=revisor        → pergunta Prazo (ex: 2026-11-05 ou "amanhã")
  step=prazo          → pergunta Descrição (opcional)
  step=descricao      → mostra card de confirmação com botão ✅ Criar / ❌ Cancelar
  action=confirmar    → cria no Notion e notifica
  action=cancelar     → aborta

Estado da sessão armazenado em memória por user_id (chave = user_id).
"""

import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Adiciona transcricao/ ao path para importar criar_tarefas_notion
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR.parent / "agente-performance" / ".env")

from criar_tarefas_notion import (
    criar_card_notion,
    USUARIOS,
    REVISOR_PADRAO,
)

logger = logging.getLogger(__name__)

# ── Usuários ordenados para exibição ─────────────────────────────────────────
USUARIOS_UNICOS = {
    "Aline Satur":        "3d5d872b-594c-811b-bebe-00025d163708",
    "Bianca Garbin":      "184d872b-594c-814f-8946-0002dcaefdcf",
    "Eros Vital":         "261d872b-594c-81c9-b194-000213f99aed",
    "Guilherme Oliveira": "3bcd872b-594c-81ec-9fa2-0002afed6b7a",
    "Isabela Rodrigues":  "183d872b-594c-81c0-86c8-0002c0bab1be",
    "Matheus Ferreira":   "1f2d872b-594c-81ef-8323-0002da953a20",
    "Miguel Ticianeli":   "dab34a81-2f1b-4708-b930-b8f54da78e7f",
}

CLIENTES_LISTA = ["Source Of", "POD", "Botica", "Liv Up", "Bazar", "Todos"]

# ── Estado em memória: user_id → dict ────────────────────────────────────────
_sessions: dict[str, dict] = {}


def _session(user_id: str) -> dict:
    if user_id not in _sessions:
        _sessions[user_id] = {}
    return _sessions[user_id]


def _clear(user_id: str) -> None:
    _sessions.pop(user_id, None)


# ── Helpers de bloco Slack ────────────────────────────────────────────────────

def _botoes(label: str, opcoes: list[str], action_prefix: str) -> dict:
    """Seção com botões inline."""
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": op, "emoji": True},
                "value": op,
                "action_id": f"{action_prefix}_{op.lower().replace(' ', '_').replace('ã','a').replace('é','e')}",
            }
            for op in opcoes
        ],
    }


def _texto(msg: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": msg}}


def _divisor() -> dict:
    return {"type": "divider"}


def _parse_prazo(texto: str) -> Optional[str]:
    """Converte texto livre para ISO date. Aceita YYYY-MM-DD, DD/MM/YYYY, 'amanhã', '+N'."""
    t = texto.strip().lower()
    hoje = datetime.now().date()
    if t in ("amanhã", "amanha"):
        return (hoje + timedelta(days=1)).isoformat()
    if t.startswith("+"):
        try:
            return (hoje + timedelta(days=int(t[1:]))).isoformat()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ── Step 1: /criar-card ───────────────────────────────────────────────────────

async def cmd_criar_card(ack, command, say):
    await ack()
    user_id  = command["user_id"]
    channel  = command["channel_id"]
    _clear(user_id)
    _session(user_id).update({"step": "titulo", "channel": channel})

    await say(
        channel=channel,
        blocks=[
            _texto("🗂️ *Criar card no Notion* — vamos lá!"),
            _divisor(),
            _texto("✏️ *Qual é o título da tarefa?*\nDigite e envie como mensagem neste canal."),
        ],
        text="Criar card — Título da tarefa?",
    )


# ── Step 2: captura Título → pergunta Responsável ────────────────────────────

async def _pedir_responsavel(say, channel: str, titulo: str):
    opcoes = list(USUARIOS_UNICOS.keys())
    blocos_botoes = []
    # Divide em linhas de 4 botões (limite Slack = 5/linha, usamos 4 para folga)
    for i in range(0, len(opcoes), 4):
        blocos_botoes.append(_botoes("Responsável", opcoes[i:i+4], "resp"))

    await say(
        channel=channel,
        blocks=[
            _texto(f"✅ Título: *{titulo}*"),
            _divisor(),
            _texto("👤 *Quem é o responsável?*"),
            *blocos_botoes,
        ],
        text="Responsável?",
    )


# ── Step 3: captura Responsável → pergunta Revisor ───────────────────────────

async def _pedir_revisor(say, channel: str, responsavel: str):
    opcoes = list(USUARIOS_UNICOS.keys())
    blocos_botoes = []
    for i in range(0, len(opcoes), 4):
        blocos_botoes.append(_botoes("Revisor", opcoes[i:i+4], "rev"))

    await say(
        channel=channel,
        blocks=[
            _texto(f"✅ Responsável: *{responsavel}*"),
            _divisor(),
            _texto("🔍 *Quem revisa?*"),
            *blocos_botoes,
        ],
        text="Revisor?",
    )


# ── Step 4: captura Revisor → pergunta Prazo ─────────────────────────────────

async def _pedir_prazo(say, channel: str, revisor: str):
    await say(
        channel=channel,
        blocks=[
            _texto(f"✅ Revisor: *{revisor}*"),
            _divisor(),
            _texto(
                "📅 *Qual é o prazo?*\n"
                "Exemplos: `2026-11-10`, `10/11/2026`, `amanhã`, `+7` (em 7 dias)\n"
                "_Ou envie `pular` para usar +7 dias a partir de hoje._"
            ),
        ],
        text="Prazo?",
    )


# ── Step 5: captura Prazo → pergunta Descrição ───────────────────────────────

async def _pedir_descricao(say, channel: str, prazo: str):
    await say(
        channel=channel,
        blocks=[
            _texto(f"✅ Prazo: *{prazo}*"),
            _divisor(),
            _texto(
                "📝 *Descrição da tarefa* _(opcional)_\n"
                "Detalhe o que precisa ser feito, ou envie `pular` para deixar em branco."
            ),
        ],
        text="Descrição?",
    )


# ── Step 6: captura Descrição → pergunta Cliente ─────────────────────────────

async def _pedir_cliente(say, channel: str, descricao: str):
    blocos_botoes = [_botoes("Cliente", CLIENTES_LISTA, "cli")]
    await say(
        channel=channel,
        blocks=[
            _texto(f"✅ Descrição salva."),
            _divisor(),
            _texto("🏢 *Para qual cliente?*"),
            *blocos_botoes,
        ],
        text="Cliente?",
    )


# ── Step 7: captura Cliente → mostra confirmação ─────────────────────────────

async def _mostrar_confirmacao(say, channel: str, sess: dict):
    titulo       = sess.get("titulo", "")
    responsavel  = sess.get("responsavel", "")
    revisor      = sess.get("revisor", "")
    prazo        = sess.get("prazo", "")
    descricao    = sess.get("descricao", "") or "_sem descrição_"
    cliente      = sess.get("cliente", "")

    await say(
        channel=channel,
        blocks=[
            _texto("📋 *Confirmar criação do card no Notion?*"),
            _divisor(),
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Título:*\n{titulo}"},
                    {"type": "mrkdwn", "text": f"*Responsável:*\n{responsavel}"},
                    {"type": "mrkdwn", "text": f"*Revisor:*\n{revisor}"},
                    {"type": "mrkdwn", "text": f"*Prazo:*\n{prazo}"},
                    {"type": "mrkdwn", "text": f"*Cliente:*\n{cliente}"},
                    {"type": "mrkdwn", "text": f"*Descrição:*\n{descricao}"},
                ],
            },
            _divisor(),
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Criar card", "emoji": True},
                        "style": "primary",
                        "value": "confirmar",
                        "action_id": "card_confirmar",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Cancelar", "emoji": True},
                        "style": "danger",
                        "value": "cancelar",
                        "action_id": "card_cancelar",
                    },
                ],
            },
        ],
        text="Confirmar criação do card?",
    )


# ── Handler de mensagens de texto (captura steps de texto) ───────────────────

async def handle_message_criar_card(user_id: str, text: str, say, channel: str) -> bool:
    """
    Retorna True se a mensagem foi consumida pelo fluxo de criação.
    Deve ser chamado antes dos handlers genéricos do agente.
    """
    sess = _sessions.get(user_id)
    if not sess:
        return False

    step = sess.get("step")
    text = text.strip()

    if step == "titulo":
        if not text:
            await say(channel=channel, text="⚠️ O título não pode ser vazio. Tente novamente.")
            return True
        sess["titulo"] = text
        sess["step"]   = "responsavel"
        await _pedir_responsavel(say, channel, text)
        return True

    if step == "prazo":
        if text.lower() in ("pular", "skip", "-"):
            from datetime import date
            prazo = (datetime.now().date() + timedelta(days=7)).isoformat()
        else:
            prazo = _parse_prazo(text)
            if not prazo:
                await say(
                    channel=channel,
                    text="⚠️ Formato de data inválido. Use `YYYY-MM-DD`, `DD/MM/YYYY`, `amanhã` ou `+N`.",
                )
                return True
        sess["prazo"] = prazo
        sess["step"]  = "descricao"
        await _pedir_descricao(say, channel, prazo)
        return True

    if step == "descricao":
        sess["descricao"] = "" if text.lower() in ("pular", "skip", "-") else text
        sess["step"]      = "cliente"
        await _pedir_cliente(say, channel, sess["descricao"])
        return True

    return False


# ── Handlers de botões ────────────────────────────────────────────────────────

async def handle_action_responsavel(ack, body, say):
    """Qualquer action_id que começa com 'resp_'."""
    await ack()
    user_id = body["user"]["id"]
    sess    = _session(user_id)
    nome    = body["actions"][0]["value"]
    channel = body["container"]["channel_id"]

    sess["responsavel"] = nome
    sess["step"]        = "revisor"
    await _pedir_revisor(say, channel, nome)


async def handle_action_revisor(ack, body, say):
    """Qualquer action_id que começa com 'rev_'."""
    await ack()
    user_id = body["user"]["id"]
    sess    = _session(user_id)
    nome    = body["actions"][0]["value"]
    channel = body["container"]["channel_id"]

    sess["revisor"] = nome
    sess["step"]    = "prazo"
    await _pedir_prazo(say, channel, nome)


async def handle_action_cliente(ack, body, say):
    """Qualquer action_id que começa com 'cli_'."""
    await ack()
    user_id = body["user"]["id"]
    sess    = _session(user_id)
    cliente = body["actions"][0]["value"]
    channel = body["container"]["channel_id"]

    sess["cliente"] = cliente
    sess["step"]    = "confirmar"
    await _mostrar_confirmacao(say, channel, sess)


async def handle_action_confirmar(ack, body, say):
    """Botão ✅ Criar card."""
    await ack()
    user_id = body["user"]["id"]
    sess    = _sessions.get(user_id, {})
    channel = body["container"]["channel_id"]

    if not sess:
        await say(channel=channel, text="⚠️ Sessão expirada. Use `/criar-card` para recomeçar.")
        return

    titulo      = sess.get("titulo", "")
    responsavel = sess.get("responsavel", "")
    revisor     = sess.get("revisor", "")
    prazo       = sess.get("prazo", "")
    descricao   = sess.get("descricao", "")
    cliente     = sess.get("cliente", "")

    resp_id  = USUARIOS_UNICOS.get(responsavel, "dab34a81-2f1b-4708-b930-b8f54da78e7f")
    rev_id   = USUARIOS_UNICOS.get(revisor, "dab34a81-2f1b-4708-b930-b8f54da78e7f")

    tarefa = {
        "titulo":           titulo,
        "responsavel_id":   resp_id,
        "responsavel_nome": responsavel,
        "descricao":        descricao,
        "prazo_detectado":  prazo,
    }

    # Usa revisor_id explícito — monkey-patch temporário no REVISOR_PADRAO não é necessário;
    # passamos como fonte_id vazio (card manual)
    await say(channel=channel, text="⏳ Criando card no Notion...")

    try:
        # Substitui o revisor_id padrão passando diretamente
        import criar_tarefas_notion as _ctn
        _old = dict(_ctn.REVISOR_PADRAO)
        _ctn.REVISOR_PADRAO["Performance"] = rev_id
        _ctn.REVISOR_PADRAO["3P"]          = rev_id
        _ctn.REVISOR_PADRAO["Gestão"]      = rev_id

        url = criar_card_notion(
            tarefa        = tarefa,
            reuniao_data  = prazo,
            reuniao_titulo= "Manual (Slack)",
            fonte_id      = f"slack_{user_id}",
            clientes      = [cliente] if cliente and cliente != "Todos" else [],
            area          = "Performance",
        )

        _ctn.REVISOR_PADRAO.update(_old)  # restaura
    except Exception as e:
        logger.exception("Erro ao criar card Notion: %s", e)
        await say(channel=channel, text=f"❌ Erro ao criar card: {e}")
        _clear(user_id)
        return

    _clear(user_id)

    if url:
        await say(
            channel=channel,
            blocks=[
                _texto(f"✅ *Card criado com sucesso!*"),
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{titulo}*\n👤 {responsavel}  |  📅 {prazo}  |  🏢 {cliente}"},
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Abrir no Notion 🔗", "emoji": True},
                        "url": url,
                        "action_id": "notion_open",
                    },
                },
            ],
            text=f"✅ Card criado: {titulo}",
        )
    else:
        await say(channel=channel, text="❌ Falha ao criar o card no Notion. Tente novamente.")


async def handle_action_cancelar(ack, body, say):
    """Botão ❌ Cancelar."""
    await ack()
    user_id = body["user"]["id"]
    channel = body["container"]["channel_id"]
    _clear(user_id)
    await say(channel=channel, text="🚫 Criação cancelada.")


# ── Registro dos handlers no AsyncApp ────────────────────────────────────────

def registrar_handlers(app):
    """
    Chama esta função no handlers.py do agente-performance para
    plugar os handlers de /criar-card e /tutorial-transcricao no AsyncApp existente.
    """
    # Comando slash
    app.command("/criar-card")(cmd_criar_card)
    app.command("/tutorial-transcricao")(cmd_tutorial_transcricao)
    app.command("/tutorial-imagem")(cmd_tutorial_imagem)

    # Botões de responsável (action_id começa com resp_)
    app.action({"action_id": re.compile(r"^resp_")})(handle_action_responsavel)

    # Botões de revisor (action_id começa com rev_)
    app.action({"action_id": re.compile(r"^rev_")})(handle_action_revisor)

    # Botões de cliente (action_id começa com cli_)
    app.action({"action_id": re.compile(r"^cli_")})(handle_action_cliente)

    # Confirmação
    app.action("card_confirmar")(handle_action_confirmar)
    app.action("card_cancelar")(handle_action_cancelar)

    # Botão "Abrir no Notion" — só faz ack
    @app.action("notion_open")
    async def _notion_open_ack(ack):
        await ack()

    logger.info("[criar-card] Handlers registrados no Slack app")


# ── Tutorial: Agente de Transcrição ──────────────────────────────────────────────────

async def cmd_tutorial_transcricao(ack, respond):
    """Tutorial do Agente de Transcrição de Reuniões."""
    await ack()
    texto = """\
*🎓 Tutorial — Agente de Transcrição*
_Transforma reuniões gravadas em tarefas acionáveis no Notion, automaticamente._

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*O que é este agente?*

O Agente de Transcrição monitora uma pasta no Google Drive, processa automaticamente os arquivos `.docx` de atas de reunião e executa um pipeline de 4 etapas:

1️⃣ *Ingestão* — lê o DOCX e salva no banco
2️⃣ *Análise* — Claude extrai contexto, participantes e decisões
3️⃣ *Alertas* — detecta riscos, prazos urgentes e problemas
4️⃣ *Comunicação* — cria cards no Notion (database *Agenda e Registros*)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📂 Como alimentar o agente*

Basta subir o arquivo `.docx` da ata na pasta compartilhada do Google Drive. O agente roda automaticamente e processa em minutos.

*Formato esperado:*
• Arquivo `.docx` (ata exportada do Google Meet / Zoom / manual)
• Deve conter uma seção de próximas etapas no formato:
  `[Nome do Responsável] Título da tarefa`
  _ou_
  `[Nome] Título: Descrição detalhada`

*Responsáveis mapeados:*
Aline Satur · Bianca Garbin · Eros Vital · Guilherme Oliveira
Isabela Rodrigues · Matheus Ferreira · Miguel Ticianeli

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📄 O que é criado no Notion*

Cada tarefa detectada vira um card no database *Agenda e Registros* com:
• *Tarefa* — título extraído da ata
• *Responsável atual* — pessoa atribuída
• *Revisor* — default Miguel (ajustável)
• *Status* — sempre `A fazer`
• *Tipo* — `Reunião`
• *Área* — detectada automaticamente
• *Cliente* — Source Of / POD / Botica / Liv Up etc.
• *Data* — prazo detectado na ata, ou data da reunião +7 dias
• *Descrição* — contexto + rastreabilidade da reunião de origem

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*➕ Criar card manualmente via Slack*

Não precisa esperar uma reunião! Use `/criar-card` para criar um card direto:

1️⃣ `/criar-card` — inicia o fluxo
2️⃣ Digite o *título* da tarefa
3️⃣ Escolha o *responsável* (botões)
4️⃣ Escolha o *revisor* (botões)
5️⃣ Informe o *prazo* (`2026-11-10`, `amanhã`, `+7`, ou `pular`)
6️⃣ Adicione uma *descrição* (ou `pular`)
7️⃣ Escolha o *cliente* (botões)
8️⃣ Confirme ✔️ — card criado com link direto para o Notion!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*🚨 Alertas automáticos*

O agente envia alertas no Slack quando detecta:
• Prazos vencidos ou prestes a vencer
• Tarefas sem responsável claro
• Riscos mencionados na ata (bloqueios, dependências externas)
• Decisões importantes que precisam de follow-up

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*⚡ Slash Commands*
`/tutorial-transcricao` — este guia
`/criar-card` — criar card manual no Notion

*Outros agentes:*
`/tutorial` — Agente de Performance (métricas)
`/tutorial-landing` — Agente de Landing Pages
🔗 *Notion:* Agenda e Registros (cards de Tipo=Reunião)
_Só você está vendo esta mensagem._
"""
    await respond(text=texto, response_type="ephemeral")


# ── Tutorial: Agente de Imagem ───────────────────────────────────────────────────

async def cmd_tutorial_imagem(ack, respond):
    """Tutorial do Agente de Geração de Imagens."""
    await ack()
    texto = """\
*🎓 Tutorial — Agente de Imagem*
_Gera criativos de mídia paga via Gemini diretamente pelo Slack._

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*O que faz?*

Recebe um pedido em português, refina o prompt com Claude (incluindo dados reais do cliente), gera a imagem via Gemini e posta o resultado em :art: *#image_gen*.

Fluxo interno:
`Pedido` → `Claude refina prompt (EN)` → `Gemini gera imagem` → `#image_gen`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*🖼️ Como gerar uma imagem*

Mencione o agente ou mande DM:
`@agente [descrição do criativo]`

Exemplos práticos:
`@agente banner de Black Friday para POD Kombucha, cores vibrantes`
`@agente story de lançamento de produto para Source OF, minimalista`
`@agente post quadrado de suplemento, fundo branco, estilo farmacêutico`
`@agente capa de campanha de verão para Botica, cores quentes`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*📍 Formatos disponíveis*

Especifique o formato no pedido:

🟦 *Banner / Horizontal* (1344×768, 16:9)
`banner`, `horizontal`, `capa`, `youtube`, `16:9`

🟨 *Story / Reels* (768×1344, 9:16)
`story`, `stories`, `reels`, `vertical`, `tiktok`, `9:16`

🟫 *Post / Feed / Quadrado* (1024×1024, 1:1) — *padrão*
`post`, `feed`, `quadrado`, `square`, `1:1`

Se não especificar, gera quadrado 1:1.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*🔄 Variações e edições*

Peça uma variação do último criativo gerado:
`@agente gere uma variação com fundo preto`
`@agente agora com o produto em destaque no centro`
`@agente mesmo estilo mas para story`

Envie uma imagem para editar ou analisar:
• _Suba a imagem_ + `@agente adiciona uma chamada de Black Friday`
• _Suba a imagem_ + `@agente o que você acha desse criativo?`

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*⚠️ Limites e regras*

• Máx *10 imagens por hora* por usuário
• Resultado sempre postado em *#image_gen* (não na thread)
• Modelos usados (em ordem de preferência):
  `gemini-2.5-flash-image` → `gemini-3.1-flash-image` → `gemini-3-pro-image`
• O prompt é refinado automaticamente com estilo do cliente (se mencionado)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*⚡ Slash Commands*
`/tutorial-imagem` — este guia
`/tutorial` — tutorial completo do Agente de Performance
`/tutorial-transcricao` — tutorial do Agente de Transcrição
_Só você está vendo esta mensagem._
"""
    await respond(text=texto, response_type="ephemeral")


import re  # noqa: E402 — necessário para registrar_handlers()
