"""
slack_criar_card.py

Fluxo conversacional 100% texto no Slack para criação de cards no Notion.
Sem botões. Todas as perguntas respondidas digitando.
Para confirmar, o usuário digita "confirmar". Para cancelar, "cancelar".

Fluxo por steps:
  /criar-card         → pergunta Título
  step=titulo         → pergunta Responsável (lista textual)
  step=responsavel    → pergunta Revisor (lista textual)
  step=revisor        → pergunta Prazo
  step=prazo          → pergunta Descrição (opcional)
  step=descricao      → pergunta Cliente (lista textual)
  step=cliente        → mostra resumo + pede "confirmar" ou "cancelar"
  step=confirmar      → cria no Notion
"""

import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

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

# ── Listas ────────────────────────────────────────────────────────────────────

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

# Lista formatada para exibição
_USUARIOS_STR = "\n".join(f"  {i+1}. {n}" for i, n in enumerate(USUARIOS_UNICOS.keys()))
_CLIENTES_STR = "  " + "  ·  ".join(CLIENTES_LISTA)

# ── Estado em memória ─────────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}

def _session(user_id: str) -> dict:
    if user_id not in _sessions:
        _sessions[user_id] = {}
    return _sessions[user_id]

def _clear(user_id: str) -> None:
    _sessions.pop(user_id, None)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_prazo(texto: str) -> Optional[str]:
    """Converte texto livre para ISO date."""
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

def _match_usuario(texto: str) -> Optional[str]:
    """Resolve nome digitado (número ou nome parcial) para nome exato."""
    t = texto.strip()
    # Por número (1-7)
    try:
        idx = int(t) - 1
        if 0 <= idx < len(USUARIOS_UNICOS):
            return list(USUARIOS_UNICOS.keys())[idx]
    except ValueError:
        pass
    # Por nome parcial (case-insensitive)
    t_lower = t.lower()
    for nome in USUARIOS_UNICOS:
        if t_lower in nome.lower():
            return nome
    return None

def _match_cliente(texto: str) -> Optional[str]:
    """Resolve cliente digitado (número ou nome parcial)."""
    t = texto.strip()
    try:
        idx = int(t) - 1
        if 0 <= idx < len(CLIENTES_LISTA):
            return CLIENTES_LISTA[idx]
    except ValueError:
        pass
    t_lower = t.lower()
    for c in CLIENTES_LISTA:
        if t_lower in c.lower():
            return c
    return None

# ── Step 1: /criar-card ───────────────────────────────────────────────────────

async def cmd_criar_card(ack, command, say):
    await ack()
    user_id   = command["user_id"]
    channel   = command["channel_id"]
    thread_ts = command.get("thread_ts")
    _clear(user_id)
    _session(user_id).update({"step": "titulo", "channel": channel, "thread_ts": thread_ts})

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        await say(**kwargs)

    await _say(text=(
        "🗂️ *Criar card no Notion* — vou fazer algumas perguntas.\n\n"
        "1️⃣ *Qual é o título da tarefa?*"
    ))

# ── Handler de mensagens de texto ─────────────────────────────────────────────

async def handle_message_criar_card(user_id: str, text: str, say, channel: str,
                                     thread_ts: str | None = None) -> bool:
    """Retorna True se a mensagem foi consumida pelo fluxo de criação de card."""
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
        await _say(text="🚫 Criação de card cancelada.")
        return True

    if step == "titulo":
        if not text:
            await _say(text="⚠️ O título não pode ser vazio. Tente novamente.")
            return True
        sess["titulo"] = text
        sess["step"]   = "responsavel"
        await _say(text=(
            f"✅ Título: *{text}*\n\n"
            f"2️⃣ *Quem é o responsável?*\n{_USUARIOS_STR}\n\n"
            "Digite o número ou parte do nome."
        ))
        return True

    if step == "responsavel":
        nome = _match_usuario(text)
        if not nome:
            await _say(text=f"⚠️ Não reconheci. Escolha pelo número ou nome:\n{_USUARIOS_STR}")
            return True
        sess["responsavel"] = nome
        sess["step"]        = "revisor"
        await _say(text=(
            f"✅ Responsável: *{nome}*\n\n"
            f"3️⃣ *Quem revisa?*\n{_USUARIOS_STR}\n\n"
            "Digite o número ou parte do nome."
        ))
        return True

    if step == "revisor":
        nome = _match_usuario(text)
        if not nome:
            await _say(text=f"⚠️ Não reconheci. Escolha pelo número ou nome:\n{_USUARIOS_STR}")
            return True
        sess["revisor"] = nome
        sess["step"]    = "prazo"
        await _say(text=(
            f"✅ Revisor: *{nome}*\n\n"
            "4️⃣ *Qual é o prazo?*\n"
            "Ex: `2026-11-10`, `10/11/2026`, `amanhã`, `+7` (em 7 dias)\n"
            "Ou `pular` para usar +7 dias a partir de hoje."
        ))
        return True

    if step == "prazo":
        if text.lower() in ("pular", "skip", "-"):
            prazo = (datetime.now().date() + timedelta(days=7)).isoformat()
        else:
            prazo = _parse_prazo(text)
            if not prazo:
                await _say(text="⚠️ Formato inválido. Use `YYYY-MM-DD`, `DD/MM/YYYY`, `amanhã` ou `+N`.")
                return True
        sess["prazo"] = prazo
        sess["step"]  = "descricao"
        await _say(text=(
            f"✅ Prazo: *{prazo}*\n\n"
            "5️⃣ *Descrição da tarefa* (opcional)\n"
            "Detalhe o que precisa ser feito, ou `pular` para deixar em branco."
        ))
        return True

    if step == "descricao":
        sess["descricao"] = "" if text.lower() in ("pular", "skip", "-") else text
        sess["step"]      = "cliente"
        clientes_num = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(CLIENTES_LISTA))
        await _say(text=(
            "6️⃣ *Para qual cliente?*\n"
            f"{clientes_num}\n\n"
            "Digite o número ou parte do nome."
        ))
        return True

    if step == "cliente":
        cliente = _match_cliente(text)
        if not cliente:
            clientes_num = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(CLIENTES_LISTA))
            await _say(text=f"⚠️ Não reconheci. Escolha:\n{clientes_num}")
            return True
        sess["cliente"] = cliente
        sess["step"]    = "confirmar"
        titulo      = sess.get("titulo", "")
        responsavel = sess.get("responsavel", "")
        revisor     = sess.get("revisor", "")
        prazo       = sess.get("prazo", "")
        descricao   = sess.get("descricao", "") or "_sem descrição_"
        await _say(text=(
            "📋 *Resumo do card — revise:*\n\n"
            f"• *Título:* {titulo}\n"
            f"• *Responsável:* {responsavel}\n"
            f"• *Revisor:* {revisor}\n"
            f"• *Prazo:* {prazo}\n"
            f"• *Cliente:* {cliente}\n"
            f"• *Descrição:* {descricao}\n\n"
            "Digite *confirmar* para criar o card ou *cancelar* para abortar."
        ))
        return True

    if step == "confirmar":
        if text.lower() == "confirmar":
            await _criar_no_notion(user_id, sess, _say)
            return True
        else:
            await _say(text="Digite *confirmar* para criar ou *cancelar* para abortar.")
            return True

    return False

# ── Criação no Notion ─────────────────────────────────────────────────────────

async def _criar_no_notion(user_id: str, sess: dict, say):
    titulo      = sess.get("titulo", "")
    responsavel = sess.get("responsavel", "")
    revisor     = sess.get("revisor", "")
    prazo       = sess.get("prazo", "")
    descricao   = sess.get("descricao", "")
    cliente     = sess.get("cliente", "")

    resp_id = USUARIOS_UNICOS.get(responsavel, "dab34a81-2f1b-4708-b930-b8f54da78e7f")
    rev_id  = USUARIOS_UNICOS.get(revisor, "dab34a81-2f1b-4708-b930-b8f54da78e7f")

    tarefa = {
        "titulo":           titulo,
        "responsavel_id":   resp_id,
        "responsavel_nome": responsavel,
        "descricao":        descricao,
        "prazo_detectado":  prazo,
    }

    _clear(user_id)
    await say(text="⏳ Criando card no Notion...")

    try:
        import criar_tarefas_notion as _ctn
        _old = dict(_ctn.REVISOR_PADRAO)
        _ctn.REVISOR_PADRAO["Performance"] = rev_id
        _ctn.REVISOR_PADRAO["3P"]          = rev_id
        _ctn.REVISOR_PADRAO["Gestão"]      = rev_id

        url = criar_card_notion(
            tarefa         = tarefa,
            reuniao_data   = prazo,
            reuniao_titulo = "Manual (Slack)",
            fonte_id       = f"slack_{user_id}",
            clientes       = [cliente] if cliente and cliente != "Todos" else [],
            area           = "Performance",
        )
        _ctn.REVISOR_PADRAO.update(_old)
    except Exception as e:
        logger.exception("Erro ao criar card Notion: %s", e)
        await say(text=f"❌ Erro ao criar card: {e}")
        return

    if url:
        await say(text=(
            f"✅ *Card criado com sucesso!*\n"
            f"• *{titulo}*\n"
            f"👤 {responsavel}  |  📅 {prazo}  |  🏢 {cliente}\n"
            f"🔗 {url}"
        ))
    else:
        await say(text="❌ Falha ao criar o card no Notion. Tente novamente.")


# ── Tutorial: Transcrição ─────────────────────────────────────────────────────

async def cmd_tutorial_transcricao(ack, respond):
    await ack()
    texto = """\
*🎓 Tutorial — Agente de Transcrição*
_Transforma atas de reunião em tarefas acionáveis no Notion, automaticamente._

*Como alimentar o agente:*
Suba o arquivo `.docx` da ata na pasta compartilhada do Google Drive.
O agente processa automaticamente e cria os cards no Notion.

*Formato esperado na ata:*
`[Nome do Responsável] Título da tarefa`
`[Nome] Título: Descrição detalhada`

*Responsáveis mapeados:*
Aline Satur · Bianca Garbin · Eros Vital · Guilherme Oliveira
Isabela Rodrigues · Matheus Ferreira · Miguel Ticianeli

*O que é criado no Notion:*
Cada tarefa vira um card com: Título, Responsável, Revisor, Prazo, Cliente, Área e Descrição.

*Criar card manual:* `/criar-card`
*Outros agentes:* `/tutorial` · `/tutorial-landing` · `/tutorial-imagem`
_Só você está vendo esta mensagem._"""
    await respond(text=texto, response_type="ephemeral")


# ── Tutorial: Imagem ──────────────────────────────────────────────────────────

async def cmd_tutorial_imagem(ack, respond):
    await ack()
    texto = """\
*🎓 Tutorial — Agente de Imagem*
_Gera criativos de mídia paga via Gemini diretamente pelo Slack._

*Como usar:*
Mencione o agente com a descrição do criativo:
`@agente banner de Black Friday para POD Kombucha, cores vibrantes`
`@agente story de lançamento para Source OF, minimalista`
`@agente post quadrado de suplemento, fundo branco`

*Formatos:*
• Banner/Horizontal (16:9): inclua `banner`, `horizontal`, `16:9`
• Story/Reels (9:16): inclua `story`, `reels`, `vertical`
• Post/Feed (1:1): padrão se não especificar

*Variações:*
`@agente gere uma variação com fundo preto`
`@agente mesmo estilo mas para story`

*Enviar imagem para editar:*
Suba a imagem + `@agente adiciona uma chamada de Black Friday`

*Limites:* máx 10 imagens/hora por usuário.
*Resultado:* sempre postado em #image_gen.
_Só você está vendo esta mensagem._"""
    await respond(text=texto, response_type="ephemeral")


# ── Registro dos handlers ─────────────────────────────────────────────────────

def registrar_handlers(app):
    app.command("/criar-card")(cmd_criar_card)
    app.command("/tutorial-transcricao")(cmd_tutorial_transcricao)
    app.command("/tutorial-imagem")(cmd_tutorial_imagem)

    # Botão "Abrir no Notion" (só ack, sem side-effects)
    @app.action("notion_open")
    async def _notion_open_ack(ack):
        await ack()

    logger.info("[criar-card] Handlers registrados no Slack app")
