"""
landing_page/handlers.py
Registro dos handlers Slack para o agente de Landing Pages.
"""
import logging
from .flow import cmd_criar_landing
from .tutorial import cmd_tutorial_landing
from .commands import cmd_meus_projetos, cmd_minhas_refs, cmd_variar_lp

logger = logging.getLogger(__name__)


def registrar_handlers_landing(app):
    app.command("/criar-landing")(cmd_criar_landing)
    app.command("/tutorial-landing")(cmd_tutorial_landing)
    app.command("/meus-projetos")(cmd_meus_projetos)
    app.command("/minhas-refs")(cmd_minhas_refs)
    app.command("/variar-lp")(cmd_variar_lp)
    logger.info("[landing-page] Handlers registrados: /criar-landing, /meus-projetos, /minhas-refs, /variar-lp")
