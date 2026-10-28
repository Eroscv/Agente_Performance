"""
card_handlers.py
Registro dos handlers Slack para /criar-card, /status-cards, /status-atas e /reprocessar-ata.
"""
import logging
from card_flow import cmd_criar_card, handle_message_criar_card
from card_status import cmd_status_cards
from card_tutorials import cmd_tutorial_transcricao, cmd_tutorial_imagem
from card_meus import cmd_meus_cards
from card_editar import cmd_editar_card, handle_message_editar_card
from slack_status_atas import cmd_status_atas, cmd_reprocessar_ata

logger = logging.getLogger(__name__)


def registrar_handlers(app):
    app.command("/criar-card")(cmd_criar_card)
    app.command("/status-cards")(cmd_status_cards)
    app.command("/meus-cards")(cmd_meus_cards)
    app.command("/editar-card")(cmd_editar_card)
    app.command("/status-atas")(cmd_status_atas)
    app.command("/reprocessar-ata")(cmd_reprocessar_ata)
    app.command("/tutorial-transcricao")(cmd_tutorial_transcricao)
    app.command("/tutorial-imagem")(cmd_tutorial_imagem)

    @app.action("notion_open")
    async def _notion_open_ack(ack):
        await ack()

    logger.info("[criar-card] Handlers registrados: /criar-card, /status-cards, /meus-cards, /editar-card, /status-atas, /reprocessar-ata")
