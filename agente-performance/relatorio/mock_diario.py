#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Relatório diário MOCK — Source Of
Usa a infra do agente de performance (Notion + Slack) com dados simulados.
Útil para validar layout antes de ligar as APIs reais.
"""

from __future__ import annotations
import os, sys
from pathlib import Path
from datetime import date, timedelta

# Garante que o agente-performance está no path
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from notion.historico import criar_relatorio_no_historico
from notion.blocks import h2, h3, divider, callout, bullet, tabela
from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n
import requests

SLACK_WEBHOOK = "https://hooks.slack.com/services/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"

# ── Dados mock ────────────────────────────────────────────────────────────────
D1 = date.today() - timedelta(days=1)
DATA_STR = D1.strftime("%d/%m/%Y")

MOCK = {
    "shopify": {
        "receita":           18_450.75,
        "pedidos":           87,
        "ticket_medio":      212.08,
        "sessoes":           3_210,
        "sessoes_checkout":  198,
        "sessoes_compra":    87,
        "taxa_conversao":    0.0271,
        "novos_clientes":    54,
        "pct_desconto":      0.082,
        "pedidos_gt300":     23,
        "frete_cobrado":     1_230.50,
    },
    "meta": {
        "invest_imp":  3_196.28,
        "receita":     12_340.00,
        "compras":     58,
        "roas":        3.86,
        "ticket":      212.76,
        "ctr":         1.84,
        "cpc":         1.42,
        "impressoes":  148_200,
        "cliques":     2_248,
    },
    "google": {
        "invest":   980.00,
        "receita":  4_210.00,
        "compras":  20,
        "roas":     4.30,
        "ticket":   210.50,
        "ctr":      3.21,
        "cpc":      0.89,
        "impressoes": 34_500,
        "cliques":  1_102,
    },
}

invest_total = MOCK["meta"]["invest_imp"] + MOCK["google"]["invest"]
mer   = MOCK["shopify"]["receita"] / invest_total
cac   = invest_total / MOCK["shopify"]["novos_clientes"]

# ── Blocos Notion ─────────────────────────────────────────────────────────────
def montar_blocos() -> list:
    s = MOCK["shopify"]
    m = MOCK["meta"]
    g = MOCK["google"]

    blocos = [
        callout(f"Dados referentes a {DATA_STR} · MOCK · Relatória Source Of", emoji="🤖"),
        divider(),

        h2("🎯 Consolidado do Dia"),
        tabela(
            header=["Métrica", "Valor"],
            linhas=[
                ["Receita Shopify",    fmt_brl(s["receita"])],
                ["Investimento Total", fmt_brl(invest_total)],
                ["MER",               fmt_x(mer)],
                ["CAC",               fmt_brl(cac)],
                ["Novos Clientes",    fmt_n(s["novos_clientes"])],
            ]
        ),
        divider(),

        h2("📦 Shopify"),
        tabela(
            header=["Métrica", "Valor"],
            linhas=[
                ["Receita",           fmt_brl(s["receita"])],
                ["Pedidos",           fmt_n(s["pedidos"])],
                ["Ticket Médio",      fmt_brl(s["ticket_medio"])],
                ["Sessões",           fmt_n(s["sessoes"])],
                ["Taxa de Conversão", fmt_pct(s["pct_conversao"])],
                ["Novos Clientes",    fmt_n(s["novos_clientes"])],
                ["Pedidos > R$300",   fmt_n(s["pedidos_gt300"])],
                ["% Desconto",        fmt_pct(s["pct_desconto"])],
                ["Frete Cobrado",     fmt_brl(s["frete_cobrado"])],
            ]
        ),
        divider(),

        h2("📘 Meta Ads"),
        tabela(
            header=["Métrica", "Valor"],
            linhas=[
                ["Invest. + Imposto", fmt_brl(m["invest_imp"])],
                ["Receita Atribuída", fmt_brl(m["receita"])],
                ["ROAS",              fmt_x(m["roas"])],
                ["Compras",           fmt_n(m["compras"])],
                ["Ticket Médio",      fmt_brl(m["ticket"])],
                ["CTR",               fmt_pct(m["ctr"] / 100)],
                ["CPC",               fmt_brl(m["cpc"])],
                ["Impressões",        fmt_n(m["impressoes"])],
                ["Cliques",           fmt_n(m["cliques"])],
            ]
        ),
        divider(),

        h2("🟢 Google Ads"),
        tabela(
            header=["Métrica", "Valor"],
            linhas=[
                ["Investimento",      fmt_brl(g["invest"])],
                ["Receita Atribuída", fmt_brl(g["receita"])],
                ["ROAS",              fmt_x(g["roas"])],
                ["Compras",           fmt_n(g["compras"])],
                ["Ticket Médio",      fmt_brl(g["ticket"])],
                ["CTR",               fmt_pct(g["ctr"] / 100)],
                ["CPC",               fmt_brl(g["cpc"])],
                ["Impressões",        fmt_n(g["impressoes"])],
                ["Cliques",           fmt_n(g["cliques"])],
            ]
        ),
    ]
    return blocos

# ── Publicar no Notion ────────────────────────────────────────────────────────
def publicar_notion() -> dict:
    titulo    = f"🔵 Source OF · Relatório Diário · {DATA_STR} (MOCK)"
    blocos    = montar_blocos()
    resultado = criar_relatorio_no_historico(
        titulo=titulo,
        blocos=blocos,
        periodo_str=DATA_STR,
        tipo="diario",
        data_ref=D1,
        cliente_key="source_of",
        plataforma="",
    )
    return resultado

# ── Enviar Slack ──────────────────────────────────────────────────────────────
def enviar_slack(notion_url: str):
    s = MOCK["shopify"]
    m = MOCK["meta"]
    g = MOCK["google"]

    texto = (
        f"📊 *Relatório D-1 — Source OF | {DATA_STR}* _(MOCK)_\n\n"
        f"*📦 Shopify*\n"
        f"• Receita: *{fmt_brl(s['receita'])}* | Pedidos: *{fmt_n(s['pedidos'])}* | Ticket: *{fmt_brl(s['ticket_medio'])}*\n"
        f"• Sessões: *{fmt_n(s['sessoes'])}* | Conversão: *{fmt_pct(s['pct_conversao'])}* | Novos: *{fmt_n(s['novos_clientes'])}*\n\n"
        f"*📘 Meta Ads*\n"
        f"• Invest: *{fmt_brl(m['invest_imp'])}* | Receita: *{fmt_brl(m['receita'])}* | ROAS: *{fmt_x(m['roas'])}*\n\n"
        f"*🟢 Google Ads*\n"
        f"• Invest: *{fmt_brl(g['invest'])}* | Receita: *{fmt_brl(g['receita'])}* | ROAS: *{fmt_x(g['roas'])}*\n\n"
        f"*🎯 Consolidado*\n"
        f"• Invest Total: *{fmt_brl(invest_total)}* | MER: *{fmt_x(mer)}* | CAC: *{fmt_brl(cac)}*\n\n"
        f"🔗 <{notion_url}|Ver relatório completo no Notion>"
    )
    r = requests.post(SLACK_WEBHOOK, json={"text": texto})
    r.raise_for_status()
    logger.info("✅ Slack enviado")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 Gerando relatório MOCK para %s...", DATA_STR)

    logger.info("📝 Publicando no Notion...")
    resultado = publicar_notion()
    notion_url = resultado.get("url", "")
    logger.info("✅ Notion: %s", notion_url)

    logger.info("💬 Enviando no Slack...")
    enviar_slack(notion_url)

    logger.info("✅ Concluído! URL: %s", notion_url)
    print(notion_url)
