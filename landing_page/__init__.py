"""
landing_page/
Agente de Landing Pages — geração de HTML via Claude, fluxo 100% texto no Slack.
"""
from .handlers import registrar_handlers_landing
from .flow import handle_message_landing, cmd_criar_landing
from .tutorial import cmd_tutorial_landing
from .commands import (
    cmd_meus_projetos,
    cmd_minhas_refs, handle_message_minhas_refs,
    cmd_variar_lp, handle_message_variar_lp,
)

__all__ = [
    "registrar_handlers_landing",
    "handle_message_landing",
    "cmd_criar_landing",
    "cmd_tutorial_landing",
    "cmd_meus_projetos",
    "cmd_minhas_refs", "handle_message_minhas_refs",
    "cmd_variar_lp", "handle_message_variar_lp",
]

__all__ = [
    "registrar_handlers_landing",
    "handle_message_landing",
    "cmd_criar_landing",
    "cmd_tutorial_landing",
]
