"""
landing_page/handlers.py
Registro dos handlers Slack para o agente de Landing Pages.
"""
import logging
from .flow import cmd_criar_landing
from .tutorial import cmd_tutorial_landing

logger = logging.getLogger(__name__)


def registrar_handlers_landing(app):
    app.command("/criar-landing")(cmd_criar_landing)
    app.command("/tutorial-landing")(cmd_tutorial_landing)
    logger.info("[landing-page] Handlers registrados")
