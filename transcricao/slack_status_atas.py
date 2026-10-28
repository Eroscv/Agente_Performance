"""
transcricao/slack_status_atas.py

Comandos Slack para monitoramento e reprocessamento de atas:

  /status-atas [N]          — últimas N atas processadas (padrão: 10)
  /reprocessar-ata [nome]   — força reprocessamento de uma ata específica

Também expõe:
  notificar_falha_slack()   — chamada pelo IngestaoAgent quando uma ata falha
"""
import logging
import os
import re
import sqlite3
from datetime import date, timedelta, timezone, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# DB da transcrição
DB_PATH = Path(__file__).parent / "db" / "reunioes.db"

# Canal padrão para notificações de erro (lê do .env)
SLACK_ATAS_CHANNEL = os.getenv("SLACK_ATAS_CHANNEL", os.getenv("SLACK_CHANNEL_ID", ""))


# ── Helpers de banco ──────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _status_icon(status: str) -> str:
    return {"ok": "✅", "erro": "❌", "skip": "⏭️"}.get(status, "❓")


def _data_brt(iso: str) -> str:
    """Converte ISO UTC para BRT (UTC-3) e retorna string legível."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        brt = dt.astimezone(timezone(timedelta(hours=-3)))
        return brt.strftime("%d/%m %H:%M")
    except Exception:
        return iso[:16]


# ── /status-atas ──────────────────────────────────────────────────────────────

async def cmd_status_atas(ack, command, say):
    await ack()
    channel   = command["channel_id"]
    thread_ts = command.get("thread_ts")
    arg       = command.get("text", "").strip()

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        await say(**kwargs)

    try:
        limite = min(int(arg), 30) if arg.isdigit() else 10
    except ValueError:
        limite = 10

    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT fonte_id, nome, status, mensagem, criado_em "
            "FROM sync_log ORDER BY criado_em DESC LIMIT ?",
            (limite,),
        ).fetchall()

        if not rows:
            await _say(text="📭 Nenhuma ata processada ainda.")
            return

        # Métricas do dia
        hoje = date.today().isoformat()
        stats = conn.execute(
            "SELECT status, COUNT(*) as n FROM sync_log WHERE criado_em >= ? GROUP BY status",
            (hoje,),
        ).fetchall()
        contagem = {r["status"]: r["n"] for r in stats}
        ok   = contagem.get("ok", 0)
        erro = contagem.get("erro", 0)

        header = (
            f"📄 *Status das Atas — últimas {len(rows)}*\n"
            f"Hoje: ✅ {ok} processadas · ❌ {erro} com erro\n"
            + "─" * 34
        )

        linhas = [header]
        for r in rows:
            icon = _status_icon(r["status"])
            nome_curto = r["nome"][:50] + ("…" if len(r["nome"]) > 50 else "")
            msg = r["mensagem"][:60] if r["mensagem"] else ""
            data = _data_brt(r["criado_em"])
            linhas.append(f"{icon} `{nome_curto}`\n   _{msg}_ · {data} BRT")

        linhas.append(
            "\n_Para reprocessar: `/reprocessar-ata [parte do nome]`_"
        )
        await _say(text="\n\n".join(linhas))

    finally:
        conn.close()


# ── /reprocessar-ata ──────────────────────────────────────────────────────────

async def cmd_reprocessar_ata(ack, command, say):
    await ack()
    channel   = command["channel_id"]
    thread_ts = command.get("thread_ts")
    arg       = command.get("text", "").strip()

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        await say(**kwargs)

    if not arg:
        await _say(text=(
            "⚠️ Informe o nome (ou parte) da ata.\n"
            "Ex: `/reprocessar-ata Source OF 2026-10`\n\n"
            "Use `/status-atas` para ver os nomes das atas recentes."
        ))
        return

    conn = _conn()
    try:
        # Busca no sync_log por nome parcial
        rows = conn.execute(
            "SELECT DISTINCT fonte_id, nome FROM sync_log WHERE nome LIKE ? ORDER BY criado_em DESC LIMIT 5",
            (f"%{arg}%",),
        ).fetchall()

        if not rows:
            await _say(text=f"⚠️ Nenhuma ata encontrada com `{arg}` no nome.\nUse `/status-atas` para ver os nomes.")
            return

        if len(rows) > 1:
            opcoes = "\n".join(f"  • `{r['nome']}`" for r in rows)
            await _say(text=f"Encontrei {len(rows)} atas com esse nome. Seja mais específico:\n{opcoes}")
            return

        file_id = rows[0]["fonte_id"]
        nome    = rows[0]["nome"]

        await _say(text=f"🔄 Reprocessando `{nome}`...")

    finally:
        conn.close()

    # Executa reprocessamento em background
    import asyncio
    asyncio.create_task(
        _tarefa_reprocessar(file_id, nome, channel, thread_ts, say)
    )


async def _tarefa_reprocessar(file_id: str, nome: str,
                               channel: str, thread_ts, say) -> None:
    """Tarefa assíncrona: força reprocessamento removendo dedup e rodando o pipeline."""
    import sys
    from pathlib import Path as _P
    sys.path.insert(0, str(_P(__file__).parent))

    async def _notify(text: str):
        kwargs = {"channel": channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        try:
            await say(**kwargs)
        except Exception as e:
            logger.warning("[reprocessar] Falha ao notificar: %s", e)

    try:
        import sync_reunioes as _impl
        from agents.ingestao import IngestaoAgent

        service = _impl.get_drive_service()
        conn    = _impl.get_conn()

        agente = IngestaoAgent()
        # Tenta processar ANTES de apagar o dedup (fail-safe)
        resultado = agente.processar_um(service, conn, {"id": file_id, "name": nome})

        # Só remove o dedup APÓS sucesso confirmado
        conn.execute("DELETE FROM sync_log WHERE fonte_id = ? AND status = 'ok'", (file_id,))
        conn.execute("DELETE FROM reunioes WHERE fonte_id = ?", (file_id,))
        conn.commit()

        # Reinsere com status ok
        _impl.salvar_log(conn, file_id, nome, "ok",
                         f"Reprocessado: {resultado.get('dados', {}).get('titulo', nome)}")
        conn.close()

        titulo = resultado.get("dados", {}).get("titulo", nome)
        await _notify(f"✅ *Ata reprocessada com sucesso!*\nTítulo: `{titulo}`")

    except Exception as e:
        logger.exception("[reprocessar] Erro: %s", e)
        await _notify(f"❌ *Falha ao reprocessar* `{nome}`\nErro: `{str(e)[:200]}`")


# ── Notificação automática de falha ──────────────────────────────────────────

async def notificar_falha_slack(slack_client, nome: str, erro: str,
                                 channel: str = "") -> None:
    """
    Chamado pelo IngestaoAgent quando uma ata falha.
    Envia alerta no canal SLACK_ATAS_CHANNEL (ou no channel passado).
    """
    dest = channel or SLACK_ATAS_CHANNEL
    if not dest:
        logger.warning("[notif-ata] SLACK_ATAS_CHANNEL não configurado — alerta não enviado.")
        return

    try:
        await slack_client.chat_postMessage(
            channel=dest,
            text=(
                f"⚠️ *Falha ao processar ata*\n"
                f"📄 `{nome}`\n"
                f"❌ `{erro[:300]}`\n\n"
                f"_Para reprocessar: `/reprocessar-ata {nome[:40]}`_"
            ),
        )
    except Exception as e:
        logger.warning("[notif-ata] Erro ao notificar Slack: %s", e)
