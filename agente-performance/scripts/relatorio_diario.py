"""
scripts/relatorio_diario.py

Gera e envia relatório diário no Slack (Block Kit) + Notion para cada cliente+plataforma.

Formato Slack:
  - Âncora no canal: header + KPIs resumidos
  - Thread: relatório completo com tabelas Block Kit

Uso:
    python scripts/relatorio_diario.py                  # ontem
    python scripts/relatorio_diario.py --data 2026-08-14
    python scripts/relatorio_diario.py --cliente source_of
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from db.connection import query, query_one
from relatorio.builder_diario import gerar_relatorio_diario
from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n, roas_emoji, delta_str
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SLACK_TOKEN   = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "performance")

EMOJIS_CLIENTE = {"source_of": "🔵", "pod_kombucha": "🟢"}
EMOJIS_PLAT    = {"meta_ads": "📘", "google_ads": "🔴"}

LABELS_CLIENTE = {"source_of": "Source OF", "pod_kombucha": "POD Kombucha"}
LABELS_PLAT    = {"meta_ads": "Meta Ads",   "google_ads": "Google Ads"}


# ── API Slack ─────────────────────────────────────────────────────────────────

def _post(payload: dict) -> dict:
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json; charset=utf-8"},
        json=payload, timeout=15,
    )
    return r.json()




# ── Block Kit helpers ─────────────────────────────────────────────────────────

def _bk_header(txt: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": txt, "emoji": True}}

def _bk_section(txt: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": txt}}

def _bk_divider() -> dict:
    return {"type": "divider"}

def _bk_context(txt: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": txt}]}
def _tabela_codigo(header: list[str], rows: list[list[str]]) -> str:
    """Tabela alinhada dentro de bloco ```código```."""
    all_rows = [header] + rows
    ncols = len(header)
    widths = [max(len(str(r[i])) for r in all_rows if i < len(r)) for i in range(ncols)]
    sep = "─" * (sum(widths) + 3 * (ncols - 1))

    def fmt(row):
        return " │ ".join(str(row[i] if i < len(row) else "").ljust(widths[i]) for i in range(ncols))

    lines = [sep, fmt(header), sep] + [fmt(r) for r in rows] + [sep]
    return "```\n" + "\n".join(lines) + "\n```"


def _chunks(lst, n=49):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]


# ── Consultas de dados ────────────────────────────────────────────────────────

def _resumo_dia(data: str, cliente_key: str, plataforma: str) -> dict | None:
    return query_one("""
        SELECT
            COUNT(DISTINCT campanha)                               AS campanhas,
            ROUND(SUM(impressoes), 0)                             AS impressoes,
            ROUND(SUM(cliques), 0)                                AS cliques,
            ROUND(SUM(alcance), 0)                                AS alcance,
            ROUND(SUM(investimento), 2)                          AS investimento,
            ROUND(SUM(conversoes), 0)                            AS conversoes,
            ROUND(SUM(receita), 2)                               AS receita,
            ROUND(SUM(receita)/NULLIF(SUM(investimento),0), 2)   AS roas,
            ROUND(SUM(cliques)/NULLIF(SUM(impressoes),0)*100, 2) AS ctr,
            ROUND(SUM(investimento)/NULLIF(SUM(cliques),0), 2)   AS cpc,
            ROUND(SUM(investimento)/NULLIF(SUM(impressoes),0)*1000, 2) AS cpm
        FROM v_performance
        WHERE data = ? AND cliente_key = ? AND plataforma = ?
    """, (data, cliente_key, plataforma))


def _campanhas_dia(data: str, cliente_key: str, plataforma: str) -> list[dict]:
    return query("""
        SELECT campanha,
               ROUND(investimento, 2) AS invest,
               ROUND(receita, 2)      AS receita,
               ROUND(roas, 2)         AS roas,
               ROUND(conversoes, 0)   AS conv,
               ROUND(cliques, 0)      AS cliques,
               ROUND(ctr, 2)          AS ctr,
               ROUND(cpc_medio, 2)    AS cpc
        FROM v_performance
        WHERE data = ? AND cliente_key = ? AND plataforma = ?
        ORDER BY investimento DESC
        LIMIT 15
    """, (data, cliente_key, plataforma))


def _shopify_dia(data: str) -> dict | None:
    return query_one("""
        SELECT receita, pedidos, novos_clientes, recompradores,
               ticket_medio, total_desconto, pedidos_acima_300
        FROM shopify_receita_diaria WHERE data = ?
    """, (data,))


def _dia_anterior(data: str, cliente_key: str, plataforma: str) -> dict | None:
    d = (date.fromisoformat(data) - timedelta(days=1)).isoformat()
    return _resumo_dia(d, cliente_key, plataforma)


def _alertas(data: str, cliente_key: str, plataforma: str) -> list[dict]:
    return query("""
        SELECT campanha,
               ROUND(investimento, 2) AS invest,
               ROUND(roas, 2)         AS roas,
               ROUND(conversoes, 0)   AS conv
        FROM v_performance
        WHERE data = ? AND cliente_key = ? AND plataforma = ?
          AND investimento > 50
          AND (roas IS NULL OR roas < 1.5 OR conversoes = 0)
        ORDER BY investimento DESC LIMIT 5
    """, (data, cliente_key, plataforma))


# ── Builder de blocos Block Kit ───────────────────────────────────────────────

def _blocos_relatorio(data: str, cliente_key: str, plataforma: str,
                       d: dict, camps: list, alts: list,
                       ant: dict | None, shopify: dict | None,
                       notion_url: str | None) -> list[dict]:
    """Monta lista completa de blocos Block Kit — tudo em tabelas de código."""

    nome_c   = LABELS_CLIENTE.get(cliente_key, cliente_key)
    nome_p   = LABELS_PLAT.get(plataforma, plataforma)
    ec       = EMOJIS_CLIENTE.get(cliente_key, "📊")
    ep       = EMOJIS_PLAT.get(plataforma, "📊")
    data_fmt = f"{data[8:10]}/{data[5:7]}/{data[:4]}"
    roas_e   = roas_emoji(d.get("roas"))
    invest_imp = round(float(d.get("investimento", 0)) * 1.1215, 2) if plataforma == "meta_ads" else None

    blocos: list[dict] = []

    # ── 1. Visão Geral ────────────────────────────────────────────────────
    blocos.append(_bk_section("*📊 Visão Geral*"))
    vg_rows = [
        ["💰 Investimento",       fmt_brl(d.get("investimento"))],
        ["💵 Receita",            fmt_brl(d.get("receita"))],
        [f"{roas_e} ROAS",        fmt_x(d.get("roas"))],
        ["🛒 Conversões",         fmt_n(d.get("conversoes"))],
        ["📊 Campanhas Ativas",   str(d.get("campanhas", "—"))],
        ["👥 Alcance",            fmt_n(d.get("alcance"))],
    ]
    if invest_imp:
        vg_rows.insert(1, ["💸 c/ Imposto (1,1215x)",  fmt_brl(invest_imp)])
    blocos.append(_bk_section(_tabela_codigo(["Métrica", "Valor"], vg_rows)))
    blocos.append(_bk_divider())

    # ── 2. Funil de Conversão ─────────────────────────────────────────────
    ctr_v    = d.get("ctr")
    conv_r   = (float(d.get("conversoes") or 0) / float(d.get("cliques") or 1) * 100) if d.get("cliques") else None
    blocos.append(_bk_section("*🔽 Funil de Conversão*"))
    funil_rows = [
        ["👁️  Impressões",  fmt_n(d.get("impressoes")),   "100%"],
        ["🖱️  Cliques",     fmt_n(d.get("cliques")),      fmt_pct(ctr_v) + " CTR"],
        ["✅  Conversões",  fmt_n(d.get("conversoes")),   fmt_pct(conv_r) + " conv/clique" if conv_r else "—"],
    ]
    blocos.append(_bk_section(_tabela_codigo(["Etapa", "Volume", "Taxa"], funil_rows)))
    blocos.append(_bk_divider())

    # ── 3. Métricas de Custo ──────────────────────────────────────────────
    blocos.append(_bk_section("*📐 Métricas de Custo*"))
    mc_rows = [
        ["🎯 CTR",       fmt_pct(d.get("ctr"))],
        ["💲 CPC",       fmt_brl(d.get("cpc"))],
        ["📢 CPM",       fmt_brl(d.get("cpm"))],
    ]
    blocos.append(_bk_section(_tabela_codigo(["Métrica", "Valor"], mc_rows)))
    blocos.append(_bk_divider())

    # ── 4. Comparativo Dia Anterior ───────────────────────────────────────
    if ant:
        blocos.append(_bk_section("*📈 Comparativo — Dia Anterior*"))
        comp_rows = [
            ["💰 Investimento", fmt_brl(ant.get("investimento")), fmt_brl(d.get("investimento")),
             delta_str(d.get("investimento"), ant.get("investimento"), False)],
            ["💵 Receita",      fmt_brl(ant.get("receita")),      fmt_brl(d.get("receita")),
             delta_str(d.get("receita"),      ant.get("receita"))],
            ["ROAS",            fmt_x(ant.get("roas")),           fmt_x(d.get("roas")),
             delta_str(d.get("roas"),         ant.get("roas"))],
            ["🛒 Conversões",   fmt_n(ant.get("conversoes")),     fmt_n(d.get("conversoes")),
             delta_str(d.get("conversoes"),   ant.get("conversoes"))],
            ["🎯 CTR",          fmt_pct(ant.get("ctr")),          fmt_pct(d.get("ctr")),
             delta_str(d.get("ctr"),          ant.get("ctr"))],
        ]
        blocos.append(_bk_section(_tabela_codigo(["Métrica", "Ontem", "Hoje", "Var."], comp_rows)))
        blocos.append(_bk_divider())

    # ── 5. Desempenho por Campanha ────────────────────────────────────────
    if camps:
        blocos.append(_bk_section("*🏆 Desempenho por Campanha*"))
        camp_rows = []
        for c in camps:
            nome = (c.get("campanha") or "—")[:36]
            re   = roas_emoji(c.get("roas"))
            camp_rows.append([
                nome,
                fmt_brl(c.get("invest")),
                fmt_brl(c.get("receita")),
                f"{re} {fmt_x(c.get('roas'))}",
                fmt_n(c.get("conv")),
            ])
        blocos.append(_bk_section(_tabela_codigo(
            ["Campanha", "Invest.", "Receita", "ROAS", "Conv."],
            camp_rows,
        )))
        blocos.append(_bk_divider())

    # ── 6. Alertas ────────────────────────────────────────────────────────
    if alts:
        alerta_rows = []
        for a in alts:
            conv  = float(a.get("conv")  or 0)
            roas_v = a.get("roas")
            nome   = (a.get("campanha") or "—")[:36]
            motivo = "Sem conversão" if conv == 0 else f"ROAS {roas_v:.2f}x"
            alerta_rows.append([nome, fmt_brl(a.get("invest")), motivo])
        if alerta_rows:
            blocos.append(_bk_section("*⚠️ Alertas — Campanhas para Revisar*"))
            blocos.append(_bk_section(_tabela_codigo(
                ["Campanha", "Invest.", "Motivo"],
                alerta_rows,
            )))
            blocos.append(_bk_divider())

    # ── 7. Shopify (Source OF) ────────────────────────────────────────────
    if shopify and cliente_key == "source_of":
        blocos.append(_bk_section("*🛒 Shopify — Dados da Loja*"))
        shp_rows = [
            ["📦 Pedidos",           fmt_n(shopify.get("pedidos"))],
            ["💰 Receita",           fmt_brl(shopify.get("receita"))],
            ["🆕 Novos Clientes",    fmt_n(shopify.get("novos_clientes"))],
            ["🔁 Recompradores",     fmt_n(shopify.get("recompradores"))],
            ["🏷️  Ticket Médio",    fmt_brl(shopify.get("ticket_medio"))],
            ["🎟️  Total Desconto",  fmt_brl(shopify.get("total_desconto"))],
            ["🏅 Pedidos >R$300",    fmt_n(shopify.get("pedidos_acima_300"))],
        ]
        blocos.append(_bk_section(_tabela_codigo(["Métrica", "Valor"], shp_rows)))
        blocos.append(_bk_divider())

    # ── 8. Rodapé ─────────────────────────────────────────────────────────
    rodape = f"_Gerado em {time.strftime('%d/%m/%Y %H:%M')} · Dados via API direta_"
    if notion_url:
        rodape += f" · <{notion_url}|📎 Ver no Notion>"
    blocos.append(_bk_context(rodape))

    return blocos

# ── Envio ─────────────────────────────────────────────────────────────────────

def _enviar_blocos(channel: str, thread_ts: str, blocos: list[dict], fallback_txt: str):
    """Envia blocos em fatias de 49 (limite Slack = 50)."""
    primeira = True
    for fatia in _chunks(blocos, 49):
        payload = {
            "channel": channel,
            "thread_ts": thread_ts,
            "blocks": fatia,
            "text": fallback_txt,
        }
        resp = _post(payload)
        if not resp.get("ok"):
            logger.error("[Slack] Falha ao enviar bloco: %s", resp.get("error"))
            if primeira: return False
        primeira = False
        time.sleep(0.3)
    return True


def enviar_relatorio_cliente(data: str, cliente_key: str, plataforma: str) -> bool:
    """Publica no Notion + envia âncora + thread Block Kit no Slack."""

    d = _resumo_dia(data, cliente_key, plataforma)
    if not d or not d.get("investimento"):
        logger.info("[%s·%s] Sem dados para %s — pulando", cliente_key, plataforma, data)
        return False

    camps   = _campanhas_dia(data, cliente_key, plataforma)
    alts    = _alertas(data, cliente_key, plataforma)
    ant     = _dia_anterior(data, cliente_key, plataforma)
    shopify = _shopify_dia(data) if cliente_key == "source_of" else None

    # ── Publica no Notion ─────────────────────────────────────────────────
    notion_url = None
    try:
        res = gerar_relatorio_diario(cliente_key=cliente_key, plataforma=plataforma, data=data, publicar=True)
        notion_url = res.get("url")
        logger.info("[%s·%s] Notion: %s", cliente_key, plataforma, notion_url)
    except Exception as e:
        logger.warning("[%s·%s] Notion falhou: %s", cliente_key, plataforma, e)

    # ── Preparação ────────────────────────────────────────────────────────
    nome_c   = LABELS_CLIENTE.get(cliente_key, cliente_key)
    nome_p   = LABELS_PLAT.get(plataforma, plataforma)
    ec       = EMOJIS_CLIENTE.get(cliente_key, "📊")
    ep       = EMOJIS_PLAT.get(plataforma, "📊")
    data_fmt = f"{data[8:10]}/{data[5:7]}/{data[:4]}"
    titulo   = f"{ec} {nome_c} · {ep} {nome_p} · Relatório Diário · {data_fmt}"

    # ── Msg 1: só o título (texto puro, sem blocos) ───────────────────────
    titulo_resp = _post({
        "channel": SLACK_CHANNEL,
        "text": titulo,
    })
    if not titulo_resp.get("ok"):
        logger.error("[%s·%s] Título falhou: %s", cliente_key, plataforma, titulo_resp.get("error"))
        return False

    thread_ts = titulo_resp.get("ts")
    logger.info("[%s·%s] Título postado ts=%s", cliente_key, plataforma, thread_ts)

    # ── Msg 2 (thread): resumo em tabela ────────────────────────────────────
    roas_e = roas_emoji(d.get("roas"))
    invest_imp = round(float(d.get("investimento", 0)) * 1.1215, 2) if plataforma == "meta_ads" else None
    resumo_rows = [
        [f"{roas_e} ROAS",       fmt_x(d.get("roas"))],
        ["💰 Investimento",      fmt_brl(d.get("investimento"))],
        ["💵 Receita",           fmt_brl(d.get("receita"))],
        ["🛒 Conversões",        fmt_n(d.get("conversoes"))],
        ["📊 Campanhas",         str(d.get("campanhas", "—"))],
    ]
    if invest_imp:
        resumo_rows.insert(2, ["💸 c/ Imposto",  fmt_brl(invest_imp)])
    ancora_blocos = [
        _bk_section(_tabela_codigo(["Métrica", "Valor"], resumo_rows)),
        _bk_context(f"_{data_fmt} · {nome_c} · {nome_p} · {d.get('campanhas', 0)} campanhas ativas_"),
    ]
    _post({
        "channel": SLACK_CHANNEL,
        "thread_ts": thread_ts,
        "blocks": ancora_blocos,
        "text": f"Resumo · {titulo}",
    })

    # ── Msg 3+ (thread): relatório completo ──────────────────────────────
    blocos = _blocos_relatorio(data, cliente_key, plataforma, d, camps, alts, ant, shopify, notion_url)
    ok = _enviar_blocos(SLACK_CHANNEL, thread_ts, blocos,
                        fallback_txt=f"Relatório diário {nome_c} · {nome_p} · {data_fmt}")
    if ok:
        logger.info("[%s·%s] Relatório completo postado na thread", cliente_key, plataforma)
    return ok


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data",    default=None, help="YYYY-MM-DD (padrão: ontem)")
    parser.add_argument("--cliente", default=None, help="Filtrar por cliente_key")
    args = parser.parse_args()

    data = args.data or (date.today() - timedelta(days=1)).isoformat()
    logger.info("Gerando relatório diário para %s", data)

    clientes_json = json.loads((BASE_DIR / "clientes.json").read_text())
    segmentos = []
    for key, cfg in clientes_json["clientes"].items():
        if not cfg.get("ativo", False): continue
        if args.cliente and key != args.cliente: continue
        for plat, pcfg in cfg.get("plataformas", {}).items():
            if pcfg.get("ativo", False):
                segmentos.append((key, plat))

    enviados = 0
    for cliente_key, plataforma in segmentos:
        ok = enviar_relatorio_cliente(data, cliente_key, plataforma)
        if ok: enviados += 1
        time.sleep(1)

    logger.info("Relatórios enviados: %d/%d", enviados, len(segmentos))


if __name__ == "__main__":
    main()
