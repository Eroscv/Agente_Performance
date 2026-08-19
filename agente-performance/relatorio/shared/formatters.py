"""
relatorio/shared/formatters.py

Formatadores de valores: BRL, %, ROAS, números.
"""
import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from db.connections import LEGACY_DB_PATH as DB_PATH
from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n
from notion.blocks import h2, h3, paragrafo, divider, callout, bullet, toggle, codigo, tabela

logger = logging.getLogger(__name__)


def _fmt_brl(v) -> str:
    if v is None:
        return "—"
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_x(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):.2f}x"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):.2f}%"


def _fmt_n(v, decimais=0) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.{decimais}f}".replace(",", ".")


# ── Builder de blocos Notion ──────────────────────────────────────────────────


def _fmt_data_pt(data_str: str) -> str:
    """Formata data ISO como 'd de mes_pt de ano' (ex: '1 de jun. de 2026')."""
    try:
        d = date.fromisoformat(str(data_str))
        return f"{d.day} de {_MESES_PT_ABR[d.month]} de {d.year}"
    except Exception:
        return str(data_str)



