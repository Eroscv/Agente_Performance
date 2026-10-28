"""
landing_page/async_generator.py

Geração de LP em background:
  - Salva projeto com status='gerando'
  - Lança asyncio.create_task para gerar sem bloquear o handler Slack
  - Notifica o usuário no mesmo canal quando pronto (ou em caso de falha)
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def gerar_lp_async(user_id: str, sess: dict, slack_client, channel: str,
                          thread_ts: str | None = None) -> None:
    """
    Lança a geração em background e retorna imediatamente.
    O usuário recebe confirmação de recebimento; a LP chega quando pronta.
    """
    from .db.queries import salvar_projeto, atualizar_status_projeto
    from .generator import gerar_lp

    # Cria o projeto com status 'gerando' para rastreamento
    projeto_id = salvar_projeto(
        user_id=user_id,
        cliente=sess.get("cliente", ""),
        objetivo=sess.get("objetivo", ""),
        publico=sess.get("publico", ""),
        tom=sess.get("tom", ""),
        extras=sess.get("extras", ""),
        refs_boas=sess.get("refs_boas", ""),
        refs_ruins=sess.get("refs_ruins", ""),
        status="gerando",
    )
    sess["_projeto_id_pre"] = projeto_id

    async def _say(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        await slack_client.chat_postMessage(**kwargs)

    await _say(text=(
        f"⚙️ *LP em geração* (projeto *#{projeto_id}*)\n"
        f"Estou gerando a landing page para _{sess.get('cliente', '')}_ em background.\n"
        "Você receberá uma notificação aqui assim que estiver pronta (geralmente ~30s).\n"
        "_Fique à vontade para usar outros comandos enquanto isso._"
    ))

    # Lança em background
    asyncio.create_task(
        _tarefa_geracao(user_id, sess, slack_client, channel, thread_ts, projeto_id)
    )


async def _tarefa_geracao(user_id: str, sess: dict, slack_client,
                           channel: str, thread_ts: str | None, projeto_id: int) -> None:
    """Tarefa assíncrona que executa em background e notifica quando pronta."""
    from .db.queries import atualizar_status_projeto, salvar_html
    from .generator import gerar_lp

    async def _notify(**kwargs):
        kwargs.setdefault("channel", channel)
        if thread_ts:
            kwargs.setdefault("thread_ts", thread_ts)
        try:
            await slack_client.chat_postMessage(**kwargs)
        except Exception as e:
            logger.warning("[LP-async] Falha ao notificar: %s", e)

    try:
        # Passa _projeto_id_pre para o generator não criar duplicata
        await gerar_lp(user_id, sess, _notify, channel, thread_ts)

    except Exception as e:
        logger.exception("[LP-async] Erro na tarefa de geração: %s", e)
        atualizar_status_projeto(projeto_id, "falha", str(e))
        await _notify(text=(
            f"❌ *Falha ao gerar LP #{projeto_id}*\n"
            f"Erro: `{str(e)[:200]}`\n"
            "Use `/criar-landing` para tentar novamente."
        ))
