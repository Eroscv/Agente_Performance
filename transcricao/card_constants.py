"""
card_constants.py
Listas, mapeamentos e estado em memória para o agente /criar-card e /status-cards.
"""
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR.parent / "agente-performance" / ".env")

# ── Usuários ──────────────────────────────────────────────────────────────────

USUARIOS_UNICOS: dict[str, str] = {
    "Aline Satur":        "3d5d872b-594c-811b-bebe-00025d163708",
    "Bianca Garbin":      "184d872b-594c-814f-8946-0002dcaefdcf",
    "Eros Vital":         "261d872b-594c-81c9-b194-000213f99aed",
    "Guilherme Oliveira": "3bcd872b-594c-81ec-9fa2-0002afed6b7a",
    "Isabela Rodrigues":  "183d872b-594c-81c0-86c8-0002c0bab1be",
    "Matheus Ferreira":   "1f2d872b-594c-81ef-8323-0002da953a20",
    "Miguel Ticianeli":   "dab34a81-2f1b-4708-b930-b8f54da78e7f",
}

_USUARIOS_STR = "\n".join(f"  {i+1}. {n}" for i, n in enumerate(USUARIOS_UNICOS.keys()))

# ── Clientes e Frentes ────────────────────────────────────────────────────────

CLIENTES_LISTA = ["Source Of", "POD", "Botica", "Liv Up", "Bazar", "Todos"]
FRENTES_LISTA  = ["ADS", "Social", "Conta", "Relatoria", "Planejamento", "Site", "Dados", "Gestão"]

_FRENTES_STR = "\n".join(f"  {i+1}. {f}" for i, f in enumerate(FRENTES_LISTA))

# ── Estado em memória ─────────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}


def get_session(user_id: str) -> dict:
    if user_id not in _sessions:
        _sessions[user_id] = {}
    return _sessions[user_id]


def clear_session(user_id: str) -> None:
    _sessions.pop(user_id, None)
