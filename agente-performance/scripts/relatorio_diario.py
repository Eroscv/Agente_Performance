"""
scripts/relatorio_diario.py

Gera e envia relatório diário no Slack para todos os clientes + plataformas.
Formato: âncora no canal + relatório completo na thread.

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
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SLACK_TOKEN   = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", "performance")

EMOJIS = {
    "source_of":    "🔵",
    "pod_kombucha": "🟢",
    "meta_ads":     "📘",
    "google_ads":   "🔴",
    "shopify":      "🛒",
}

LABELS = {
    "source_of":    "Source OF",
    "pod_kombucha": "POD Kombucha",
    "meta_ads":     "Meta Ads",
    "google_ads":   "Google Ads",
}


def _post(payload: dict) -> dict:
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json; charset=utf-8"},
        json=payload,
        timeout=15,
    )
    return r.json()


def _fmt_brl(v) -> str:
    if v is None: return "—"
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_n(v, dec=0) -> str:
    if v is None: return "—"
    return f"{float(v):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_x(v) -> str:
    if v is None: return "—"
    return f"{float(v):.2f}x"


def _fmt_pct(v) -> str:
    if v is None: return "—"
    return f"{float(v):.2f}%"


def _roas_emoji(roas) -> str:
    if roas is None: return "⚪"
    r = float(roas)
    if r >= 4:   return "🟢"
    if r >= 2:   return "🟡"
    return "🔴"


def _tabela(linhas: list[list[str]], header: list[str] = None) -> str:
    """Monta tabela ASCII para Slack (monospace via blocos section)."""
    todas = ([header] if header else []) + linhas
    # Larguras
    ncols = max(len(l) for l in todas)
    larguras = [0] * ncols
    for linha in todas:
        for i, cel in enumerate(linha):
            larguras[i] = max(larguras[i], len(str(cel)))
    sep = "─" * (sum(larguras) + ncols * 3 - 1)
    rows = []
    if header:
        rows.append(" │ ".join(str(c).ljust(larguras[i]) for i, c in enumerate(header)))
        rows.append(sep)
    for linha in linhas:
        rows.append(" │ ".join(str(c).ljust(larguras[i]) for i, c in enumerate(linha)))
    return "\n".join(rows)


def buscar_dados_dia(data: str, cliente_key: str, plataforma: str) -> dict | None:
    """Busca dados agregados de um cliente+plataforma em uma data."""
    r = query_one(f"""
        SELECT
            COUNT(DISTINCT campanha)             AS campanhas,
            ROUND(SUM(impressoes), 0)            AS impressoes,
            ROUND(SUM(cliques), 0)               AS cliques,
            ROUND(SUM(alcance), 0)               AS alcance,
            ROUND(SUM(investimento), 2)          AS investimento,
            ROUND(SUM(conversoes), 0)            AS conversoes,
            ROUND(SUM(receita), 2)               AS receita,
            ROUND(SUM(receita)/NULLIF(SUM(investimento),0), 2) AS roas,
            ROUND(SUM(cliques)/NULLIF(SUM(impressoes),0)*100, 2) AS ctr,
            ROUND(SUM(investimento)/NULLIF(SUM(cliques),0), 2)  AS cpc,
            ROUND(SUM(investimento)/NULLIF(SUM(impressoes),0)*1000, 2) AS cpm
        FROM v_performance
        WHERE data = ? AND cliente_key = ? AND plataforma = ?
    """, (data, cliente_key, plataforma))

    if not r or not r.get("investimento"):
        return None
    return r


def buscar_top_campanhas(data: str, cliente_key: str, plataforma: str, limit: int = 5) -> list[dict]:
    return query("""
        SELECT campanha,
               ROUND(investimento, 2) AS invest,
               ROUND(receita, 2)      AS receita,
               ROUND(roas, 2)         AS roas,
               ROUND(conversoes, 0)   AS conv,
               ROUND(cliques, 0)      AS cliques
        FROM v_performance
        WHERE data = ? AND cliente_key = ? AND plataforma = ?
        ORDER BY investimento DESC
        LIMIT ?
    """, (data, cliente_key, plataforma, limit))


def buscar_shopify(data: str) -> dict | None:
    return query_one("""
        SELECT receita, pedidos, novos_clientes, recompradores,
               ticket_medio, total_desconto, pedidos_acima_300
        FROM source_of_shopify_diario
        WHERE data = ?
    """, (data,))


def buscar_alertas(data: str, cliente_key: str, plataforma: str) -> list[dict]:
    return query("""
        SELECT campanha,
               ROUND(investimento, 2) AS invest,
               ROUND(roas, 2)         AS roas,
               ROUND(conversoes, 0)   AS conv
        FROM v_performance
        WHERE data = ? AND cliente_key = ? AND plataforma = ?
          AND investimento > 50
          AND (roas IS NULL OR roas < 1.5 OR conversoes = 0)
        ORDER BY investimento DESC
        LIMIT 5
    """, (data, cliente_key, plataforma))


def montar_relatorio_bloco(data: str, cliente_key: str, plataforma: str,
                            dados: dict, tops: list, alertas: list,
                            shopify: dict = None) -> str:
    """Monta o texto do relatório detalhado para a thread."""
    emoji_c   = EMOJIS.get(cliente_key, "📊")
    emoji_p   = EMOJIS.get(plataforma, "📊")
    nome_c    = LABELS.get(cliente_key, cliente_key)
    nome_p    = LABELS.get(plataforma, plataforma)
    data_fmt  = f"{data[8:10]}/{data[5:7]}/{data[:4]}"
    roas_e    = _roas_emoji(dados.get("roas"))
    invest_imp = round(float(dados.get("investimento", 0)) * 1.1215, 2) if plataforma == "meta_ads" else None

    linhas_visao = [
        ["💰 Investimento",        _fmt_brl(dados.get("investimento"))],
    ]
    if invest_imp:
        linhas_visao.append(["💰 +Imposto (1,1215x)", _fmt_brl(invest_imp)])
    linhas_visao += [
        ["💵 Receita",             _fmt_brl(dados.get("receita"))],
        [f"{roas_e} ROAS",         _fmt_x(dados.get("roas"))],
        ["🛒 Conversões",          _fmt_n(dados.get("conversoes"))],
        ["📊 Campanhas Ativas",    str(dados.get("campanhas", "—"))],
    ]

    linhas_funil = [
        ["👁️  Impressões",  _fmt_n(dados.get("impressoes")),  "—"],
        ["🖱️  Cliques",     _fmt_n(dados.get("cliques")),     _fmt_pct(dados.get("ctr"))],
        ["✅  Conversões",  _fmt_n(dados.get("conversoes")),  "—"],
    ]

    linhas_avancadas = [
        ["🎯 CTR",       _fmt_pct(dados.get("ctr"))],
        ["💲 CPC",       _fmt_brl(dados.get("cpc"))],
        ["📢 CPM",       _fmt_brl(dados.get("cpm"))],
        ["👥 Alcance",   _fmt_n(dados.get("alcance"))],
    ]

    txt = f"*{emoji_c} {nome_c} · {emoji_p} {nome_p} · Relatório Diário · {data_fmt}*\n\n"

    txt += "*📊 Visão Geral*\n"
    txt += "───────────────────────────────────\n"
    txt += _tabela(linhas_visao, ["Métrica", "Resultado"]) + "\n\n"

    txt += "*🔽 Funil de Conversão*\n"
    txt += "──────────────────────────────────────\n"
    txt += _tabela(linhas_funil, ["Etapa", "Volume", "Taxa"]) + "\n\n"

    txt += "*📐 Métricas Avançadas*\n"
    txt += "───────────────────────────────\n"
    txt += _tabela(linhas_avancadas, ["Métrica", "Valor"]) + "\n\n"

    if tops:
        txt += "*🏆 Top Campanhas*\n"
        txt += "──────────────────────────────────────────────────────────\n"
        for t in tops:
            nome = t["campanha"][:40] if t["campanha"] else "—"
            alerta = " ⚠️" if t.get("roas") and float(t["roas"]) < 1.5 else ""
            txt += f"• {nome} — {_fmt_brl(t['invest'])} | ROAS {_fmt_x(t['roas'])} | {_fmt_n(t['conv'])} conv{alerta}\n"
        txt += "\n"

    if shopify and cliente_key == "source_of":
        txt += "*🛒 Shopify — Dados da Loja*\n"
        txt += "───────────────────────────────────────────\n"
        linhas_shp = [
            ["📦 Pedidos",           _fmt_n(shopify.get("pedidos"))],
            ["💰 Receita",           _fmt_brl(shopify.get("receita"))],
            ["🆕 Novos Clientes",    _fmt_n(shopify.get("novos_clientes"))],
            ["🔁 Recompradores",     _fmt_n(shopify.get("recompradores"))],
            ["🏷️  Ticket Médio",     _fmt_brl(shopify.get("ticket_medio"))],
            ["🎟️  Total Desconto",   _fmt_brl(shopify.get("total_desconto"))],
            ["🏅 Pedidos >R$300",    _fmt_n(shopify.get("pedidos_acima_300"))],
        ]
        txt += _tabela(linhas_shp, ["Métrica", "Valor"]) + "\n\n"

    if alertas:
        txt += "*⚠️ Alertas*\n"
        for a in alertas:
            txt += f"• {a['campanha'][:45]} — R$ {a['invest']} | ROAS {_fmt_x(a['roas'])} | {_fmt_n(a['conv'])} conv\n"
        txt += "\n"

    txt += f"_Gerado em {time.strftime('%d/%m/%Y %H:%M')} · Dados via API direta_"
    return txt


def enviar_relatorio_cliente(data: str, cliente_key: str, plataforma: str,
                              shopify: dict = None) -> bool:
    """Publica no Notion + envia âncora + thread no Slack."""
    dados   = buscar_dados_dia(data, cliente_key, plataforma)
    if not dados:
        logger.info("[%s·%s] Sem dados para %s — pulando", cliente_key, plataforma, data)
        return False

    tops    = buscar_top_campanhas(data, cliente_key, plataforma)
    alertas = buscar_alertas(data, cliente_key, plataforma)

    # ── Publica no Notion ──────────────────────────────────────────────────
    notion_url = None
    try:
        resultado_notion = gerar_relatorio_diario(
            cliente_key=cliente_key,
            plataforma=plataforma,
            data=data,
            publicar=True,
        )
        notion_url = resultado_notion.get("url")
        logger.info("[%s·%s] Notion publicado: %s", cliente_key, plataforma, notion_url)
    except Exception as e:
        logger.warning("[%s·%s] Falha no Notion: %s", cliente_key, plataforma, e)

    emoji_c  = EMOJIS.get(cliente_key, "📊")
    nome_c   = LABELS.get(cliente_key, cliente_key)
    nome_p   = LABELS.get(plataforma, plataforma)
    data_fmt = f"{data[8:10]}/{data[5:7]}/{data[:4]}"
    roas_e   = _roas_emoji(dados.get("roas"))

    # ── Âncora no canal ────────────────────────────────────────────────────
    ancora_txt = (
        f"{emoji_c} *{nome_c} · {nome_p} · Relatório Diário · {data_fmt}*\n"
        f"> {roas_e} ROAS: *{_fmt_x(dados.get('roas'))}* · "
        f"Invest: *{_fmt_brl(dados.get('investimento'))}* · "
        f"Receita: *{_fmt_brl(dados.get('receita'))}* · "
        f"Conv: *{_fmt_n(dados.get('conversoes'))}*"
    )

    ancora_resp = _post({
        "channel": SLACK_CHANNEL,
        "text": ancora_txt,
        "mrkdwn": True,
    })

    if not ancora_resp.get("ok"):
        logger.error("[%s·%s] Âncora falhou: %s", cliente_key, plataforma, ancora_resp.get("error"))
        return False

    thread_ts = ancora_resp.get("ts")
    logger.info("[%s·%s] Âncora postada ts=%s", cliente_key, plataforma, thread_ts)

    # ── Thread — relatório detalhado ─────────────────────────────────────
    relatorio_txt = montar_relatorio_bloco(
        data, cliente_key, plataforma, dados, tops, alertas,
        shopify=shopify if cliente_key == "source_of" else None
    )

    # Adiciona link Notion no rodapé da thread
    if notion_url:
        relatorio_txt += f"\n\n<{notion_url}|\ud83d� Ver relatório completo no Notion>"

    resp = _post({
        "channel": SLACK_CHANNEL,
        "thread_ts": thread_ts,
        "text": relatorio_txt,
        "mrkdwn": True,
    })

    if not resp.get("ok"):
        logger.error("[%s·%s] Thread falhou: %s", cliente_key, plataforma, resp.get("error"))
        return False

    logger.info("[%s·%s] Relatório completo postado na thread", cliente_key, plataforma)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data",    default=None, help="YYYY-MM-DD (padrão: ontem)")
    parser.add_argument("--cliente", default=None, help="Filtrar por cliente_key")
    args = parser.parse_args()

    data = args.data or (date.today() - timedelta(days=1)).isoformat()
    logger.info("Gerando relatório diário para %s", data)

    # Segmentos ativos — lê do clientes.json
    clientes_json = json.loads((BASE_DIR / "clientes.json").read_text())
    segmentos = []
    for key, cfg in clientes_json["clientes"].items():
        if not cfg.get("ativo", False):
            continue
        if args.cliente and key != args.cliente:
            continue
        for plat, pcfg in cfg.get("plataformas", {}).items():
            if pcfg.get("ativo", False):
                segmentos.append((key, plat))

    shopify = buscar_shopify(data)

    enviados = 0
    for cliente_key, plataforma in segmentos:
        ok = enviar_relatorio_cliente(data, cliente_key, plataforma, shopify=shopify)
        if ok:
            enviados += 1
        time.sleep(1)  # pausa entre relatórios

    logger.info("Relatórios enviados: %d/%d", enviados, len(segmentos))


if __name__ == "__main__":
    main()
