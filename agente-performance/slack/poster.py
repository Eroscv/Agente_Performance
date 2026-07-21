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


# ── Relatório Source OF detalhado ─────────────────────────────────────────────

def _blocos_source_of(resultado: dict, periodo_str: str, tipo_label: str) -> list[dict]:
    r = resultado.get("resumo", {})
    url = resultado.get("url", "")
    num_camp = resultado.get("num_campanhas", "—")

    # KPIs principais
    roas = r.get("roas")
    roas_str = _fmt_x(roas)
    roas_emoji = "🟢" if roas and float(roas) >= 3 else ("🟡" if roas and float(roas) >= 1.5 else "🔴")

    ctr = r.get("ctr")
    ctr_str = _fmt_pct(ctr)

    blocks = [
        _bloco_header(f"🔵 Source OF · Meta Ads · Relatório {tipo_label} · {periodo_str}"),
        _bloco_divider(),

        # ── KPIs ──────────────────────────────────────────────────────────
        _bloco_section("*📊 Visão Geral*"),
        _bloco_section(
            _tabela_txt(
                ["Métrica", "Resultado"],
                [
                    ["💰 Investimento", _fmt_brl(r.get("total_invest"))],
                    ["💰 +Imposto (1,1215x)", _fmt_brl(r.get("invest_imp"))],
                    ["💵 Receita Meta", _fmt_brl(r.get("total_receita"))],
                    ["🛒 Receita Shopify", _fmt_brl(r.get("receita_shopify"))],
                    [f"{roas_emoji} ROAS", roas_str],
                    ["🛒 Compras", _fmt_n(r.get("total_compras"))],
                    ["💲 CPA", _fmt_brl(r.get("cpa"))],
                    ["📊 Campanhas Ativas", str(num_camp)],
                ],
            )
        ),
        _bloco_divider(),

        # ── Funil ─────────────────────────────────────────────────────────
        _bloco_section("*🔽 Funil de Conversão*"),
        _bloco_section(
            _tabela_txt(
                ["Etapa", "Volume", "Taxa"],
                [
                    ["👁️  Impressões",  _fmt_n(r.get("impressoes_total")),   "—"],
                    ["🖱️  Cliques",      _fmt_n(r.get("cliques_total")),      _fmt_pct(ctr)],
                    ["🛒  Carrinhos",    _fmt_n(r.get("carrinho_total")),     "—"],
                    ["💳  Checkouts",    _fmt_n(r.get("checkout_total")),     "—"],
                    ["✅  Compras",      _fmt_n(r.get("total_compras")),      "—"],
                ],
            )
        ),
        _bloco_divider(),

        # ── Métricas avançadas ────────────────────────────────────────────
        _bloco_section("*📐 Métricas Avançadas*"),
        _bloco_section(
            _tabela_txt(
                ["Métrica", "Valor"],
                [
                    ["🎯 CTR",          ctr_str],
                    ["💲 CPC",          _fmt_brl(r.get("cpc_medio"))],
                    ["📢 CPM",          _fmt_brl(r.get("cpm_medio"))],
                    ["🔁 Frequência",   _fmt_n(r.get("frequencia_media"), 2)],
                    ["👥 Alcance",      _fmt_n(r.get("alcance_total"))],
                ],
            )
        ),
    ]

    # ── Shopify ───────────────────────────────────────────────────────────
    shopify = resultado.get("shopify", {})
    if shopify:
        blocks += [
            _bloco_divider(),
            _bloco_section("*📦 Shopify — Dados da Loja*"),
            _bloco_section(
                _tabela_txt(
                    ["Métrica", "Valor"],
                    [
                        ["📦 Pedidos",       _fmt_n(shopify.get("total_pedidos"))],
                        ["💰 Receita",       _fmt_brl(shopify.get("receita_total"))],
                        ["🆕 Novos Clientes", _fmt_n(shopify.get("novos_clientes"))],
                        ["🔁 Taxa Recompra", _fmt_pct(shopify.get("taxa_recompra"))],
                        ["🏅 MER",           _fmt_x(shopify.get("mer"))],
                    ],
                )
            ),
        ]

    # ── Link Notion ───────────────────────────────────────────────────────
    blocks += [
        _bloco_divider(),
        _bloco_context(
            f"_Gerado em {time.strftime('%d/%m/%Y %H:%M')} · "
            + (f"<{url}|📎 Ver relatório completo no Notion>" if url else "Notion: não publicado")
            + "_"
        ),
    ]

    return blocks


# ── Relatório POD Kombucha detalhado ──────────────────────────────────────────

def _blocos_pod(resultado: dict, periodo_str: str, tipo_label: str) -> list[dict]:
    r = resultado.get("resumo", {})
    url = resultado.get("url", "")

    roas = r.get("roas")
    roas_str = _fmt_x(roas)
    roas_emoji = "🟢" if roas and float(roas) >= 3 else ("🟡" if roas and float(roas) >= 1.5 else "🔴")

    ctr = r.get("ctr")
    invest = r.get("invest", 0) or 0
    conversoes = r.get("conversoes", 0) or 0
    cpa = (invest / conversoes) if conversoes > 0 else None

    blocks = [
        _bloco_header(f"🟢 POD Kombucha · Google Ads · Relatório {tipo_label} · {periodo_str}"),
        _bloco_divider(),

        # ── KPIs ──────────────────────────────────────────────────────────
        _bloco_section("*📊 Visão Geral*"),
        _bloco_section(
            _tabela_txt(
                ["Métrica", "Resultado"],
                [
                    ["💰 Investimento",    _fmt_brl(invest)],
                    ["💵 Receita (Conv.)", _fmt_brl(r.get("receita"))],
                    [f"{roas_emoji} ROAS",  roas_str],
                    ["🛒 Conversões",      _fmt_n(conversoes, 1)],
                    ["💲 CPA",            _fmt_brl(cpa)],
                    ["📊 Campanhas",      _fmt_n(r.get("num_campanhas"))],
                ],
            )
        ),
        _bloco_divider(),

        # ── Funil ─────────────────────────────────────────────────────────
        _bloco_section("*🔽 Funil de Conversão*"),
        _bloco_section(
            _tabela_txt(
                ["Etapa", "Volume", "Taxa"],
                [
                    ["👁️  Impressões", _fmt_n(r.get("impressoes")), "—"],
                    ["🖱️  Cliques",    _fmt_n(r.get("cliques")),    _fmt_pct(ctr)],
                    ["✅  Conversões", _fmt_n(conversoes, 1),        "—"],
                ],
            )
        ),
        _bloco_divider(),

        # ── Métricas avançadas ────────────────────────────────────────────
        _bloco_section("*📐 Métricas Avançadas*"),
        _bloco_section(
            _tabela_txt(
                ["Métrica", "Valor"],
                [
                    ["🎯 CTR",  _fmt_pct(ctr)],
                    ["💲 CPC",  _fmt_brl(r.get("cpc"))],
                    ["📢 CPM",  _fmt_brl(r.get("cpm"))],
                ],
            )
        ),
    ]

    # ── Campanhas do período ───────────────────────────────────────────────
    campanhas = resultado.get("campanhas", [])
    if campanhas:
        camp_rows = []
        for c in sorted(campanhas, key=lambda x: x.get("invest", 0) or 0, reverse=True):
            c_roas = c.get("roas") or (c.get("receita", 0) / c.get("invest", 1) if c.get("invest") else None)
            camp_rows.append([
                c.get("campanha", "—")[:35],
                _fmt_brl(c.get("invest")),
                _fmt_n(c.get("conversoes"), 1),
                _fmt_brl(c.get("receita")),
                _fmt_x(c_roas),
            ])

        blocks += [
            _bloco_divider(),
            _bloco_section("*🏆 Performance por Campanha*"),
            _bloco_section(
                _tabela_txt(
                    ["Campanha", "Invest.", "Conv.", "Receita", "ROAS"],
                    camp_rows,
                )
            ),
        ]

    # ── Link Notion ───────────────────────────────────────────────────────
    blocks += [
        _bloco_divider(),
        _bloco_context(
            f"_Gerado em {time.strftime('%d/%m/%Y %H:%M')} · "
            + (f"<{url}|📎 Ver relatório completo no Notion>" if url else "Notion: não publicado")
            + "_"
        ),
    ]

    return blocks


# ── Função principal: ancora + thread ────────────────────────────────────────

def postar_relatorio(
    resultado: dict,
    periodo_str: str,
    tipo_label: str,
    cliente: str,          # "source_of" | "pod_kombucha"
    channel: str = None,
) -> bool:
    """
    Posta relatório no Slack em 2 etapas:
      1. Mensagem âncora no canal
      2. Relatório DETALHADO como reply na thread da âncora

    Args:
        resultado:   dict retornado por gerar_relatorio_* (resumo, url, campanhas, shopify)
        periodo_str: ex: "13/07/2026 → 19/07/2026"
        tipo_label:  "Semanal" | "Mensal" | "Diário"
        cliente:     "source_of" | "pod_kombucha"
        channel:     canal Slack (usa env se None)
    """
    ch = (channel or _channel()).lstrip("#")

    # ── 1. Mensagem âncora ────────────────────────────────────────────────
    if cliente == "source_of":
        ancora_txt = f"📊 *Relatório {tipo_label} · Source OF · Meta Ads*\n_{periodo_str}_"
        icon = "🔵"
    else:
        ancora_txt = f"📊 *Relatório {tipo_label} · POD Kombucha · Google Ads*\n_{periodo_str}_"
        icon = "🟢"

    ancora_resp = _post_api("chat.postMessage", {
        "channel": ch,
        "text": ancora_txt,
        "mrkdwn": True,
    })

    if not ancora_resp.get("ok"):
        logger.error("[Slack] Falha ao postar âncora: %s", ancora_resp.get("error"))
        return False

    thread_ts = ancora_resp.get("ts")
    logger.info("[Slack] Âncora postada: ts=%s canal=%s", thread_ts, ch)

    # ── 2. Relatório detalhado na thread ──────────────────────────────────
    if cliente == "source_of":
        blocks = _blocos_source_of(resultado, periodo_str, tipo_label)
    else:
        blocks = _blocos_pod(resultado, periodo_str, tipo_label)

    # Slack permite 50 blocos/mensagem — envia em fatias se necessário
    primeira = True
    for fatia in _chunks(blocks, 49):
        payload = {
            "channel": ch,
            "thread_ts": thread_ts,
            "blocks": fatia,
            "text": f"{icon} Relatório {tipo_label} — detalhes",
        }
        resp = _post_api("chat.postMessage", payload)
        if not resp.get("ok"):
            logger.error("[Slack] Falha ao postar bloco na thread: %s", resp.get("error"))
            if primeira:
                return False
        primeira = False
        time.sleep(0.3)  # rate-limit gentil

    logger.info("[Slack] Relatório detalhado postado na thread ts=%s", thread_ts)
    return True


def postar_webhook_fallback(msg: str) -> bool:
    """Fallback simples via webhook quando a Bot API não estiver disponível."""
    import urllib.request, json
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return False
    try:
        payload = json.dumps({"text": msg}).encode()
        req = urllib.request.Request(webhook_url, data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error("[Slack webhook] %s", e)
        return False
