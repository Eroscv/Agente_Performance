"""
card_handlers.py
Registro dos handlers Slack para /criar-card e /status-cards.
"""
import logging
from card_flow import cmd_criar_card, handle_message_criar_card
from card_status import cmd_status_cards
from card_tutorials import cmd_tutorial_transcricao, cmd_tutorial_imagem

logger = logging.getLogger(__name__)


def registrar_handlers(app):
    app.command("/criar-card")(cmd_criar_card)
    app.command("/status-cards")(cmd_status_cards)
    app.command("/tutorial-transcricao")(cmd_tutorial_transcricao)
    app.command("/tutorial-imagem")(cmd_tutorial_imagem)

    @app.action("notion_open")
    async def _notion_open_ack(ack):
        await ack()

    logger.info("[criar-card] Handlers registrados")
