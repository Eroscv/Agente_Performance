"""
slack/poster.py

Posta relatórios e respostas no Slack com estrutura âncora + thread.

Fluxo de relatório:
  1. Mensagem âncora no canal:  "📊 Relatório Semanal 13/07–19/07 · Source OF"
  2. Relatório DETALHADO na thread dessa mensagem (como reply)

Fluxo de consulta (@mention):
  - Resposta detalhada na thread da mensagem original

Requer: SLACK_BOT_TOKEN e SLACK_CHANNEL no .env
"""

import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE = "https://slack.com/api"
_CHANNEL_ENV_KEYS = ["SLACK_CHANNEL", "FLORA_SLACK_CHANNEL"]
_TIMEOUT = 15


def _token() -> str:
    return os.getenv("SLACK_BOT_TOKEN", "")


def _channel() -> str:
    """Resolve o canal do .env — remove '#' se presente."""
    for key in _CHANNEL_ENV_KEYS:
        val = os.getenv(key, "").strip().lstrip("#")
        if val:
            return val
    return "performance"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }


def _post_api(endpoint: str, payload: dict, tentativas: int = 3) -> dict:
    """POST para API Slack com retry."""
    url = f"{_BASE}/{endpoint}"
    for i in range(1, tentativas + 1):
        try:
            r = requests.post(url, headers=_headers(), json=payload, timeout=_TIMEOUT)
            data = r.json()
            if data.get("ok"):
                return data
            err = data.get("error", "unknown")
            logger.warning("[Slack] %s falhou (tentativa %d/%d): %s", endpoint, i, tentativas, err)
            if err in ("ratelimited",):
                time.sleep(2 ** i)
        except Exception as e:
            logger.warning("[Slack] %s erro (tentativa %d/%d): %s", endpoint, i, tentativas, e)
            if i < tentativas:
                time.sleep(2 ** i)
    return {}


# ── Bloco de tabela em código fixo ───────────────────────────────────────────

def _tabela_txt(header: list[str], rows: list[list[str]], sep: str = " │ ") -> str:
    """Monta tabela alinhada para bloco ```código``` no Slack."""
    all_rows = [header] + rows
    widths = [max(len(str(r[i])) for r in all_rows) for i in range(len(header))]

    def pad(val, w):
        return str(val).ljust(w)

    linha_sep = "─" * (sum(widths) + len(sep) * (len(header) - 1))
    lines = [linha_sep]
    lines.append(sep.join(pad(h, widths[i]) for i, h in enumerate(header)))
    lines.append(linha_sep)
    for row in rows:
        lines.append(sep.join(pad(row[i] if i < len(row) else "—", widths[i]) for i in range(len(header))))
    lines.append(linha_sep)
    return "```\n" + "\n".join(lines) + "\n```"


# ── Formatadores detalhados ───────────────────────────────────────────────────

def _fmt_brl(v) -> str:
    if v is None:
        return "—"
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


def _fmt_x(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}x"
    except Exception:
        return "—"


def _fmt_n(v, decimais=0) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{decimais}f}".replace(",", ".")
    except Exception:
        return "—"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}%"
    except Exception:
        return "—"


def _delta(atual, anterior, maior_melhor=True) -> str:
    """Retorna emoji de variação ↑ / ↓ / → comparando com período anterior."""
    try:
        a, b = float(atual), float(anterior)
        if b == 0:
            return ""
        pct = (a - b) / abs(b) * 100
        if abs(pct) < 1:
            return f"→ ({pct:+.1f}%)"
        up = pct > 0
        sinal = "↑" if up else "↓"
        emoji = "✅" if (up == maior_melhor) else "⚠️"
        return f"{emoji} {sinal} {abs(pct):.1f}%"
    except Exception:
        return ""


# ── Blocos Slack Block Kit ────────────────────────────────────────────────────

def _bloco_header(txt: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": txt, "emoji": True}}


def _bloco_section(txt: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": txt}}


def _bloco_divider() -> dict:
    return {"type": "divider"}


def _bloco_context(txt: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": txt}]}
def _chunks(blocks: list, size: int = 49):
    """Divide lista de blocos em fatias (limite Slack: 50/msg)."""
    for i in range(0, len(blocks), size):
        yield blocks[i : i + size]
