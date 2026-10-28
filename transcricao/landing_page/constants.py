"""
landing_page/constants.py
Configurações globais e estado em memória para o agente de Landing Pages.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR.parent / "agente-performance" / ".env")

LP_CHANNEL = (
    os.getenv("SLACK_IMAGE_GEN_CHANNEL_ID")
    or os.getenv("SLACK_IMAGE_GEN_CHANNEL", "image_gen")
)

# ── Estado em memória ─────────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}


def get_session(user_id: str) -> dict:
    if user_id not in _sessions:
        _sessions[user_id] = {}
    return _sessions[user_id]


def clear_session(user_id: str) -> None:
    _sessions.pop(user_id, None)
