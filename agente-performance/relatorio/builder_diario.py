"""
relatorio/builder_diario.py

Builder de relatório diário para Notion + Slack.

Gera blocos Notion no mesmo padrão dos relatórios semanais/mensais
e publica no histórico de cada cliente.

Uso:
    from relatorio.builder_diario import gerar_relatorio_diario
    resultado = gerar_relatorio_diario(cliente_key="source_of", plataforma="meta_ads", data="2026-08-14")
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from db.connection import query, query_one
from notion.historico import criar_relatorio_no_historico

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent

LABELS = {
    "source_of":    "Source OF",
    "pod_kombucha": "POD Kombucha",
    "meta_ads":     "Meta Ads",
    "google_ads":   "Google Ads",
}

EMOJIS_CLIENTE = {
    "source_of":    "🔵",
    "pod_kombucha": "🟢",
}


# ── Helpers de formatação ─────────────────────────────────────────────────────

def _fmt_brl(v) -> str:
    if v is None: return "—"
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def _fmt_x(v) -> str:
    if v is None: return "—"
    return f"{float(v):.2f}x"

def _fmt_pct(v) -> str:
    if v is None: return "—"
    return f"{float(v):.2f}%"

def _fmt_n(v, dec=0) -> str:
    if v is None: return "—"
    return f"{float(v):,.{dec}f}".replace(",", ".")

def _roas_emoji(roas) -> str:
    if roas is None: return "⚪"
    r = float(roas)
    if r >= 4:  return "🟢"
    if r >= 2:  return "🟡"
    return "🔴"


# ── Blocos Notion ─────────────────────────────────────────────────────────────


def _h2(texto: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": texto}}]}}


def _paragrafo(texto: str, bold: bool = False) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": texto},
                                          "annotations": {"bold": bold}}]}}

def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}

def _callout(texto: str, emoji: str = "📊") -> dict:
    return {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": texto}}],
            "icon": {"type": "emoji", "emoji": emoji},
            "color": "gray_background",
        }
    }

def _tabela_notion(header: list[str], linhas: list[list[str]]) -> dict:
    """Bloco tabela Notion com cabeçalho."""
    def _cel(txt: str) -> list:
        return [{"type": "text", "text": {"content": str(txt)}}]

    rows = []
    # Header
    rows.append({
        "type": "table_row",
        "table_row": {"cells": [_cel(h) for h in header]}
    })
    for linha in linhas:
        rows.append({
            "type": "table_row",
            "table_row": {"cells": [_cel(c) for c in linha]}
        })
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(header),
            "has_column_header": True,
            "has_row_header": False,
            "children": rows,
        }
    }

def _bullet(texto: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": texto}}]}}


# ── Consultas de dados ────────────────────────────────────────────────────────

def _buscar_resumo_dia(data: str, cliente_key: str, plataforma: str) -> dict | None:
    return query_one("""
        SELECT
            COUNT(DISTINCT campanha)                          AS campanhas,
            ROUND(SUM(impressoes), 0)                        AS impressoes,
            ROUND(SUM(cliques), 0)                           AS cliques,
            ROUND(SUM(alcance), 0)                           AS alcance,
            ROUND(SUM(investimento), 2)                      AS investimento,
            ROUND(SUM(conversoes), 0)                        AS conversoes,
            ROUND(SUM(receita), 2)                           AS receita,
            ROUND(SUM(receita)/NULLIF(SUM(investimento),0), 2) AS roas,
            ROUND(SUM(cliques)/NULLIF(SUM(impressoes),0)*100, 4) AS ctr,
            ROUND(SUM(investimento)/NULLIF(SUM(cliques),0), 2)  AS cpc,
            ROUND(SUM(investimento)/NULLIF(SUM(impressoes),0)*1000, 2) AS cpm
        FROM v_performance
        WHERE data = ? AND cliente_key = ? AND plataforma = ?
    """, (data, cliente_key, plataforma))


def _buscar_campanhas_dia(data: str, cliente_key: str, plataforma: str) -> list[dict]:
    return query("""
        SELECT
            campanha,
            ROUND(investimento, 2)  AS investimento,
            ROUND(receita, 2)       AS receita,
            ROUND(roas, 2)          AS roas,
            ROUND(conversoes, 0)    AS conversoes,
            ROUND(cliques, 0)       AS cliques,
            ROUND(impressoes, 0)    AS impressoes,
            ROUND(ctr, 2)           AS ctr,
            ROUND(cpc_medio, 2)     AS cpc
        FROM v_performance
        WHERE data = ? AND cliente_key = ? AND plataforma = ?
        ORDER BY investimento DESC
    """, (data, cliente_key, plataforma))


def _buscar_shopify_dia(data: str) -> dict | None:
    return query_one("""
        SELECT receita, pedidos, novos_clientes, recompradores,
               ticket_medio, total_desconto, pedidos_acima_300
        FROM source_of_shopify_diario
        WHERE data = ?
    """, (data,))


def _buscar_dia_anterior(data: str, cliente_key: str, plataforma: str) -> dict | None:
    """Busca dados do dia anterior para comparativo."""
    d = date.fromisoformat(data) - timedelta(days=1)
    return _buscar_resumo_dia(d.isoformat(), cliente_key, plataforma)


# ── Gerador de blocos ─────────────────────────────────────────────────────────

def _blocos_visao_geral(resumo: dict, plataforma: str, data_fmt: str) -> list[dict]:
    invest_imp = round(float(resumo.get("investimento", 0)) * 1.1215, 2) if plataforma == "meta_ads" else None
    roas_e = _roas_emoji(resumo.get("roas"))

    linhas = [
        ["💰 Investimento",        _fmt_brl(resumo.get("investimento"))],
    ]
    if invest_imp:
        linhas.append(["💰 +Imposto (1,1215x)", _fmt_brl(invest_imp)])
    linhas += [
        ["💵 Receita",             _fmt_brl(resumo.get("receita"))],
        [f"{roas_e} ROAS",         _fmt_x(resumo.get("roas"))],
        ["🛒 Conversões",          _fmt_n(resumo.get("conversoes"))],
        ["📊 Campanhas Ativas",    str(resumo.get("campanhas", "—"))],
    ]

    return [
        _h2("📊 Visão Geral"),
        _tabela_notion(["Métrica", "Resultado"], linhas),
        _divider(),
    ]


def _blocos_funil(resumo: dict) -> list[dict]:
    linhas = [
        ["👁️ Impressões",  _fmt_n(resumo.get("impressoes")),  "—"],
        ["🖱️ Cliques",     _fmt_n(resumo.get("cliques")),     _fmt_pct(resumo.get("ctr"))],
        ["✅ Conversões",  _fmt_n(resumo.get("conversoes")),  "—"],
    ]
    return [
        _h2("🔽 Funil de Conversão"),
        _tabela_notion(["Etapa", "Volume", "Taxa"], linhas),
        _divider(),
    ]


def _blocos_metricas_avancadas(resumo: dict) -> list[dict]:
    linhas = [
        ["🎯 CTR",      _fmt_pct(resumo.get("ctr"))],
        ["💲 CPC",      _fmt_brl(resumo.get("cpc"))],
        ["📢 CPM",      _fmt_brl(resumo.get("cpm"))],
        ["👥 Alcance",  _fmt_n(resumo.get("alcance"))],
    ]
    return [
        _h2("📐 Métricas Avançadas"),
        _tabela_notion(["Métrica", "Valor"], linhas),
        _divider(),
    ]


def _blocos_campanhas(campanhas: list[dict]) -> list[dict]:
    if not campanhas:
        return []

    # Notion limita 100 linhas por tabela (incluindo header)
    MAX_LINHAS = 99
    truncado = len(campanhas) > MAX_LINHAS
    campanhas_exibir = campanhas[:MAX_LINHAS]

    header = ["Campanha", "Invest.", "Receita", "ROAS", "Conv.", "CTR", "CPC"]
    linhas = []
    for c in campanhas_exibir:
        nome = (c.get("campanha") or "—")[:50]
        roas_e = _roas_emoji(c.get("roas"))
        linhas.append([
            nome,
            _fmt_brl(c.get("investimento")),
            _fmt_brl(c.get("receita")),
            f"{roas_e} {_fmt_x(c.get('roas'))}",
            _fmt_n(c.get("conversoes")),
            _fmt_pct(c.get("ctr")),
            _fmt_brl(c.get("cpc")),
        ])

    blocos = [
        _h2("📋 Desempenho por Campanha"),
        _tabela_notion(header, linhas),
    ]
    if truncado:
        blocos.append(_paragrafo(f"... e mais {len(campanhas) - MAX_LINHAS} anúncios. Ver Notion para lista completa."))
    blocos.append(_divider())
    return blocos


def _blocos_shopify(shopify: dict) -> list[dict]:
    if not shopify:
        return []
    linhas = [
        ["📦 Pedidos",           _fmt_n(shopify.get("pedidos"))],
        ["💰 Receita",           _fmt_brl(shopify.get("receita"))],
        ["🆕 Novos Clientes",    _fmt_n(shopify.get("novos_clientes"))],
        ["🔁 Recompradores",     _fmt_n(shopify.get("recompradores"))],
        ["🏷️ Ticket Médio",     _fmt_brl(shopify.get("ticket_medio"))],
        ["🎟️ Total Desconto",   _fmt_brl(shopify.get("total_desconto"))],
        ["🏅 Pedidos >R$300",    _fmt_n(shopify.get("pedidos_acima_300"))],
    ]
    return [
        _h2("🛒 Shopify — Dados da Loja"),
        _tabela_notion(["Métrica", "Valor"], linhas),
        _divider(),
    ]


def _blocos_comparativo(resumo: dict, anterior: dict) -> list[dict]:
    """Comparativo dia vs dia anterior."""
    def _var(atual, ant, maior_melhor=True) -> str:
        if atual is None or ant is None or float(ant) == 0:
            return "—"
        v = (float(atual) - float(ant)) / float(ant) * 100
        seta = ("↑" if v > 0 else "↓") if maior_melhor else ("↓" if v > 0 else "↑")
        return f"{seta} {abs(v):.1f}%"

    linhas = [
        ["💰 Investimento", _fmt_brl(anterior.get("investimento")), _fmt_brl(resumo.get("investimento")),
         _var(resumo.get("investimento"), anterior.get("investimento"), False)],
        ["💵 Receita",      _fmt_brl(anterior.get("receita")),      _fmt_brl(resumo.get("receita")),
         _var(resumo.get("receita"), anterior.get("receita"))],
        ["ROAS",            _fmt_x(anterior.get("roas")),           _fmt_x(resumo.get("roas")),
         _var(resumo.get("roas"), anterior.get("roas"))],
        ["🛒 Conversões",   _fmt_n(anterior.get("conversoes")),     _fmt_n(resumo.get("conversoes")),
         _var(resumo.get("conversoes"), anterior.get("conversoes"))],
        ["🎯 CTR",          _fmt_pct(anterior.get("ctr")),          _fmt_pct(resumo.get("ctr")),
         _var(resumo.get("ctr"), anterior.get("ctr"))],
    ]
    return [
        _h2("📈 Comparativo Dia Anterior"),
        _tabela_notion(["Métrica", "Ontem", "Hoje", "Var."], linhas),
        _divider(),
    ]


def _blocos_alertas(campanhas: list[dict]) -> list[dict]:
    alertas = []
    for c in campanhas:
        roas = c.get("roas")
        invest = float(c.get("investimento") or 0)
        conv = float(c.get("conversoes") or 0)
        nome = (c.get("campanha") or "—")[:50]

        if invest > 50 and conv == 0:
            alertas.append(f"⚠️ {nome} — R$ {invest:.0f} investidos sem nenhuma conversão")
        elif roas is not None and float(roas) < 1.5 and invest > 100:
            alertas.append(f"🔴 {nome} — ROAS {roas:.2f}x abaixo do breakeven (invest R$ {invest:.0f})")

    if not alertas:
        return [_h2("✅ Alertas"), _paragrafo("Nenhum alerta crítico no período."), _divider()]

    blocos = [_h2("⚠️ Alertas")]
    for a in alertas:
        blocos.append(_bullet(a))
    blocos.append(_divider())
    return blocos


# ── Função principal ──────────────────────────────────────────────────────────

def gerar_relatorio_diario(
    cliente_key: str,
    plataforma: str,
    data: str = None,
    publicar: bool = True,
) -> dict:
    """
    Gera relatório diário para um cliente+plataforma e publica no Notion.

    Args:
        cliente_key: 'source_of' | 'pod_kombucha'
        plataforma:  'meta_ads' | 'google_ads'
        data:        'YYYY-MM-DD' (padrão: ontem)
        publicar:    se False, retorna blocos sem publicar

    Returns:
        dict com url, page_id, resumo
    """
    if data is None:
        from datetime import date as _date
        data = (_date.today() - timedelta(days=1)).isoformat()

    resumo = _buscar_resumo_dia(data, cliente_key, plataforma)
    if not resumo or not resumo.get("investimento"):
        logger.warning("[Diário] Sem dados para %s·%s em %s", cliente_key, plataforma, data)
        return {"erro": "sem_dados", "data": data, "cliente_key": cliente_key, "plataforma": plataforma}

    campanhas = _buscar_campanhas_dia(data, cliente_key, plataforma)
    anterior  = _buscar_dia_anterior(data, cliente_key, plataforma)

    shopify = None
    if cliente_key == "source_of":
        shopify = _buscar_shopify_dia(data)

    # Formata data
    data_fmt  = f"{data[8:10]}/{data[5:7]}/{data[:4]}"
    nome_c    = LABELS.get(cliente_key, cliente_key)
    nome_p    = LABELS.get(plataforma, plataforma)
    emoji_c   = EMOJIS_CLIENTE.get(cliente_key, "📊")

    titulo = f"{emoji_c} {nome_c} · {nome_p} · Relatório Diário · {data_fmt}"

    # ── Monta blocos Notion ───────────────────────────────────────────────
    blocos = [
        _callout(
            f"{nome_c} · {nome_p} · {data_fmt} — "
            f"ROAS: {_fmt_x(resumo.get('roas'))} · "
            f"Invest: {_fmt_brl(resumo.get('investimento'))} · "
            f"Receita: {_fmt_brl(resumo.get('receita'))} · "
            f"Conv: {_fmt_n(resumo.get('conversoes'))}",
            emoji=emoji_c,
        ),
        _divider(),
    ]

    blocos += _blocos_visao_geral(resumo, plataforma, data_fmt)
    blocos += _blocos_funil(resumo)
    blocos += _blocos_metricas_avancadas(resumo)

    if anterior:
        blocos += _blocos_comparativo(resumo, anterior)

    blocos += _blocos_campanhas(campanhas)
    blocos += _blocos_alertas(campanhas)

    if shopify:
        blocos += _blocos_shopify(shopify)

    blocos.append(_paragrafo(
        f"Gerado em {time.strftime('%d/%m/%Y %H:%M')} · Dados via API direta: {nome_p}",
        bold=False
    ))

    if not publicar:
        return {
            "titulo":     titulo,
            "resumo":     resumo,
            "blocos":     blocos,
            "data":       data,
            "cliente_key": cliente_key,
            "plataforma": plataforma,
        }

    # ── Publica no Notion ─────────────────────────────────────────────────
    from datetime import date as _date
    data_obj = _date.fromisoformat(data)
    resultado = criar_relatorio_no_historico(
        titulo=titulo,
        blocos=blocos,
        periodo_str=data_fmt,
        tipo="diario",
        data_ref=data_obj,
        data_inicio=data_obj,
        data_fim=data_obj,
        cliente_key=cliente_key,
    )

    logger.info("[Diário] %s·%s %s → Notion: %s", cliente_key, plataforma, data, resultado.get("url"))

    return {
        "titulo":      titulo,
        "page_id":     resultado.get("page_id"),
        "url":         resultado.get("url"),
        "resumo":      resumo,
        "data":        data,
        "cliente_key": cliente_key,
        "plataforma":  plataforma,
    }
