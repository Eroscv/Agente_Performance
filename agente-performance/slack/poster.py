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

import json
import logging
import os
import time
from typing import Optional

import requests
from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n, delta_str as _delta_calc

logger = logging.getLogger(__name__)

_BASE = "https://slack.com/api"
_CHANNEL_ENV_KEYS = ["SLACK_CHANNEL"]
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
    """POST para API Slack com retry. Registra TODOS os disparos em slack_disparos_log."""
    url = f"{_BASE}/{endpoint}"

    # ── Extrai metadados do payload para o log ─────────────────────
    _channel      = str(payload.get("channel") or "")
    _thread_ts    = payload.get("thread_ts")
    _payload_size = len(json.dumps(payload).encode())
    # Preview: texto direto ou primeiro bloco de texto
    _preview = payload.get("text") or ""
    if not _preview and payload.get("blocks"):
        for _b in payload["blocks"]:
            _t = _b.get("text") or {}
            _v = _t.get("text") if isinstance(_t, dict) else str(_t)
            if _v:
                _preview = _v; break
    # Infere tipo pelo conteúdo do preview/thread
    _tipo = "thread" if _thread_ts else "outro"
    _p = (_preview or "").lower()
    if "alerta" in _p or "roas" in _p and ("abaixo" in _p or "breakeven" in _p):
        _tipo = "alerta_roas"
    elif "relatório diário" in _p or "relat\u00f3rio di\u00e1rio" in _p:
        _tipo = "relatorio_diario"
    elif "relatório semanal" in _p or "relat\u00f3rio semanal" in _p:
        _tipo = "relatorio_semanal"
    elif "relatório mensal" in _p or "relat\u00f3rio mensal" in _p:
        _tipo = "relatorio_mensal"
    elif "sync" in _p or "sincroniz" in _p:
        _tipo = "sync"
    elif "❌" in (_preview or "") or "falhou" in _p or "erro" in _p:
        _tipo = "erro"
    elif _thread_ts:
        _tipo = "thread"
    else:
        _tipo = "ancora"

    for i in range(1, tentativas + 1):
        try:
            r = requests.post(url, headers=_headers(), json=payload, timeout=_TIMEOUT)
            data = r.json()
            if data.get("ok"):
                # ── Registra disparo com sucesso ─────────────────────
                try:
                    from db.slack_log import registrar_disparo
                    registrar_disparo(
                        endpoint=endpoint,
                        channel=_channel,
                        tipo=_tipo,
                        texto_preview=_preview,
                        thread_ts=str(_thread_ts) if _thread_ts else None,
                        slack_ts=data.get("ts"),
                        ok=True,
                        payload_size=_payload_size,
                    )
                except Exception as _le:
                    logger.debug("[SlackLog] Erro no log (non-critical): %s", _le)
                return data
            err = data.get("error", "unknown")
            logger.warning("[Slack] %s falhou (tentativa %d/%d): %s", endpoint, i, tentativas, err)
            if err in ("ratelimited",):
                time.sleep(2 ** i)
        except Exception as e:
            logger.warning("[Slack] %s erro (tentativa %d/%d): %s", endpoint, i, tentativas, e)
            if i < tentativas:
                time.sleep(2 ** i)

    # ── Registra falha final ────────────────────────────────
    try:
        from db.slack_log import registrar_disparo
        registrar_disparo(
            endpoint=endpoint,
            channel=_channel,
            tipo=_tipo,
            texto_preview=_preview,
            thread_ts=str(_thread_ts) if _thread_ts else None,
            ok=False,
            erro_msg=f"falhou após {tentativas} tentativas",
            payload_size=_payload_size,
        )
    except Exception as _le:
        logger.debug("[SlackLog] Erro no log (non-critical): %s", _le)
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
