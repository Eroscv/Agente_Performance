"""
relatorio/builder.py

Orquestrador do relatório semanal Meta Ads.
Pipeline: SQLite → regras → blocos Notion → publicação.

Uso:
    from relatorio.builder import gerar_relatorio_semanal
    resultado = gerar_relatorio_semanal(cliente="source_of")
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from relatorio.regras import (
    FATOR_IMPOSTO,
    spend_com_imposto,
    calcular_cpa,
    calcular_cac,
    calcular_ticket_medio,
    calcular_pct_budget,
)
from relatorio.meta import tabela_principal, alocacao_budget, top_ads
from notion.historico import criar_relatorio_no_historico

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"

# Timeout em segundos para queries SQLite no builder (evita travar se banco estiver locked)
_SQLITE_TIMEOUT = int(os.getenv("BUILDER_SQLITE_TIMEOUT", "30"))

# Clientes mapeados para chave Notion
CLIENTES = {
    "source_of":          "Source OF",
    "consorcio_sorteado": "Consórcio Sorteado",
    "maximo_servico":     "Máximo Serviço",
    "be_mushin":          "BE MUSHIN",
    "bazar_precatorio":   "Bazar do Precatório",
}


# ── Consulta SQLite ───────────────────────────────────────────────────────────

def _buscar_campanhas(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
    incluir_atribuicao: bool = True,
) -> list[dict]:
    """
    Retorna campanhas agregadas por nome no período.

    incluir_atribuicao=True  → inclui todas as campanhas (mesmo investimento=0),
                               capturando receita de atribuição cross-campaign.
    incluir_atribuicao=False → filtra AND investimento > 0, apenas campanhas ativas.
    """
    filtro_atrib = "AND investimento > 0" if not incluir_atribuicao else ""
    sql = f"""
        SELECT
            campanha,
            SUM(investimento)          AS investimento,
            SUM(COALESCE(compras, 0))  AS compras,
            SUM(impressoes)            AS impressoes,
            SUM(COALESCE(cliques_link, 0))   AS cliques_link,
            SUM(cliques_todos)         AS cliques_todos,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2)         AS cpc_link,
            ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2)    AS cpm,
            SUM(COALESCE(valor_compras, 0))  AS valor_compras,
            SUM(COALESCE(adicoes_carrinho, 0))  AS adicoes_carrinho,
            SUM(COALESCE(checkout_iniciado, 0)) AS checkout_iniciado,
            SUM(alcance)               AS alcance,
            ROUND(CAST(SUM(COALESCE(cliques_link, 0)) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4) AS ctr_link,
            ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia,
            SUM(COALESCE(custo_por_compra, 0)) AS custo_por_compra_meta,
            ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4) AS roas_compras
        FROM campanhas
        WHERE data_inicio >= ?
          AND data_fim    <= ?
          {filtro_atrib}
        GROUP BY campanha
        ORDER BY investimento DESC
    """
    conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()
    cur.execute(sql, (data_inicio.isoformat(), data_fim.isoformat()))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _resumo_geral(campanhas: list[dict]) -> dict:
    """Agrega métricas totais da conta no período."""
    total_invest   = sum(float(c.get("investimento") or 0) for c in campanhas)
    total_compras  = sum(int(c.get("compras") or 0) for c in campanhas)
    total_impressoes = sum(int(c.get("impressoes") or 0) for c in campanhas)
    total_cliques  = sum(int(c.get("cliques_link") or 0) for c in campanhas)
    total_receita  = sum(float(c.get("valor_compras") or 0) for c in campanhas)
    total_carrinho = sum(int(c.get("adicoes_carrinho") or 0) for c in campanhas)
    total_checkout = sum(int(c.get("checkout_iniciado") or 0) for c in campanhas)
    total_alcance  = sum(int(c.get("alcance") or 0) for c in campanhas)

    # Separa receita própria (campanhas com investimento) de atribuída (sem investimento)
    receita_propria   = sum(
        float(c.get("valor_compras") or 0)
        for c in campanhas
        if float(c.get("investimento") or 0) > 0
    )
    receita_atribuida = sum(
        float(c.get("valor_compras") or 0)
        for c in campanhas
        if float(c.get("investimento") or 0) == 0
    )

    invest_imp    = spend_com_imposto(total_invest)
    # ROAS = Σ Receita / Σ Investimento (sem imposto)
    roas = round(total_receita / total_invest, 4) if total_invest > 0 else None
    # Guard: total_compras pode ser 0 quando nenhuma campanha do período tem compras
    if total_compras == 0:
        logger.warning("[_resumo_geral] total_compras=0 no período — CPA será None")

    # CPA Meta: média ponderada do custo_por_compra informado pela Meta
    cpa_meta_vals = [
        float(c.get("custo_por_compra_meta") or 0)
        for c in campanhas
        if float(c.get("custo_por_compra_meta") or 0) > 0
    ]
    cpa_meta = round(sum(cpa_meta_vals) / len(cpa_meta_vals), 2) if cpa_meta_vals else None

    # CPA ajustado: calculado com imposto sobre investimento real
    cpa_ajustado  = calcular_cpa(total_invest, total_compras) if total_compras else None

    # Ticket médio
    ticket_medio  = round(total_receita / total_compras, 2) if total_compras else None

    ctr           = (total_cliques / total_impressoes * 100) if total_impressoes else None
    cpc_medio     = (total_invest / total_cliques) if total_cliques else None
    cpm_medio     = (total_invest / total_impressoes * 1000) if total_impressoes else None
    conv_rate     = (total_compras / total_cliques * 100) if total_cliques else None
    # Frequência ponderada
    frequencia_media = round(total_impressoes / total_alcance, 2) if total_alcance else None

    return {
        "total_invest":     total_invest,
        "invest_imp":       invest_imp,
        "total_compras":    total_compras,
        "total_impressoes": total_impressoes,
        "total_cliques":    total_cliques,
        "total_receita":    total_receita,
        "receita_propria":  receita_propria,
        "receita_atribuida": receita_atribuida,
        "total_carrinho":   total_carrinho,
        "total_checkout":   total_checkout,
        "total_alcance":    total_alcance,
        "roas":             roas,
        "cpa":              cpa_ajustado,          # alias legado
        "cpa_meta":         cpa_meta,
        "cpa_ajustado":     cpa_ajustado,
        "ticket_medio":     ticket_medio,
        "ctr":              ctr,
        "cpc_medio":        cpc_medio,
        "cpm_medio":        cpm_medio,
        "conv_rate":        conv_rate,
        "frequencia_media": frequencia_media,
        "num_campanhas":    len(campanhas),
    }


def _alertas(campanhas: list[dict], resumo: dict, resumo_anterior: dict = None) -> list[str]:
    """
    Gera lista de alertas automáticos baseada nos dados.
    Se resumo_anterior fornecido, adiciona tendência ↑↓→ nos alertas de ROAS por campanha.
    """
    alertas = []

    # Mapa de ROAS anterior por campanha (para tendência em alertas individuais)
    roas_anterior_map: dict[str, float] = {}
    if resumo_anterior and isinstance(resumo_anterior, dict):
        # resumo_anterior é agregado da conta — só usamos ROAS geral
        roas_ant_geral = resumo_anterior.get("roas")
    else:
        roas_ant_geral = None

    def _tendencia_roas(roas_atual, roas_ant):
        """Retorna símbolo de tendência baseado na variação de ROAS."""
        if roas_atual is None or roas_ant is None or roas_ant == 0:
            return ""
        var_pct = (roas_atual - roas_ant) / abs(roas_ant) * 100
        if abs(var_pct) < 5:
            return " →"
        return " ↑" if var_pct > 0 else " ↓"

    # Campanhas sem conversão com investimento relevante
    sem_conv = [
        c["campanha"] for c in campanhas
        if (c.get("compras") or 0) == 0 and float(c.get("investimento") or 0) > 200
    ]
    if sem_conv:
        alertas.append(f"⚠️ {len(sem_conv)} campanha(s) com investimento > R$200 sem nenhuma compra: {', '.join(sem_conv[:3])}")

    # ROAS abaixo de 1 (com tendência vs período anterior)
    if resumo["roas"] and resumo["roas"] < 1:
        tend = _tendencia_roas(resumo["roas"], roas_ant_geral)
        era_str = f" (era {roas_ant_geral:.2f}x na semana anterior)" if roas_ant_geral else ""
        alertas.append(f"🔴 ROAS geral abaixo de 1x ({resumo['roas']:.2f}x{tend}){era_str} — investimento não está se pagando")
    elif resumo["roas"] and resumo["roas"] < 2:
        tend = _tendencia_roas(resumo["roas"], roas_ant_geral)
        era_str = f" (era {roas_ant_geral:.2f}x na semana anterior)" if roas_ant_geral else ""
        alertas.append(f"🟡 ROAS geral abaixo de 2x ({resumo['roas']:.2f}x{tend}){era_str} — atenção à rentabilidade")

    # CTR baixo
    if resumo["ctr"] and resumo["ctr"] < 1:
        ctr_ant = resumo_anterior.get("ctr") if resumo_anterior else None
        tend = _tendencia_roas(resumo["ctr"], ctr_ant)
        alertas.append(f"⚠️ CTR médio baixo ({resumo['ctr']:.2f}%{tend}) — criativos podem precisar de revisão")

    # Taxa de conversão do carrinho para compra
    if resumo["total_carrinho"] and resumo["total_compras"]:
        taxa = resumo["total_compras"] / resumo["total_carrinho"] * 100
        if taxa < 20:
            alertas.append(f"🟡 Taxa carrinho→compra baixa ({taxa:.1f}%) — possível atrito no checkout")

    if not alertas:
        alertas.append("✅ Nenhum alerta crítico no período.")

    return alertas


# ── Funções de formatação ─────────────────────────────────────────────────────

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

def _blocos_resumo(resumo: dict, periodo_str: str) -> list[dict]:
    """Bloco de resumo executivo — 3 seções distintas: KPIs, Investimento, Alcance, Conversão."""
    r = resumo

    # Cor do callout de ROAS baseada na performance
    roas_val = r.get("roas")
    if roas_val is None:
        roas_color = "gray_background"
    elif roas_val >= 3:
        roas_color = "green_background"
    elif roas_val >= 1.5:
        roas_color = "yellow_background"
    else:
        roas_color = "red_background"

    # Taxa clique→compra
    taxa_clique_compra = (
        round(r["total_compras"] / r["total_cliques"] * 100, 2)
        if r.get("total_cliques") and r["total_compras"]
        else None
    )

    # Texto KPI principal (ROAS, Receita, Compras, Ticket)
    kpi_txt = (
        f"📈 ROAS: {_fmt_x(roas_val)}  |  "
        f"💰 Receita: {_fmt_brl(r['total_receita'])}  |  "
        f"🛒 Compras: {r['total_compras']}  |  "
        f"🎫 Ticket: {_fmt_brl(r.get('ticket_medio'))}"
    )

    blocos = [
        # Banner do relatório
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content":
                    f"Relatório gerado automaticamente pelo Agente de Performance Guará · {periodo_str}"
                }}],
                "icon": {"type": "emoji", "emoji": "⚙️"},
            }
        },

        # Título da seção
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📊 Resumo Executivo"}}]}},

        # Callout colorido com KPIs principais
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": kpi_txt}}],
                "icon": {"type": "emoji", "emoji": "📈"},
                "color": roas_color,
            }
        },

        # --- Seção: Investimento ---
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "💸 Investimento"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"Meta Ads: {_fmt_brl(r['total_invest'])}"
         }}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"+ Imposto ({FATOR_IMPOSTO}×): {_fmt_brl(r['invest_imp'])}"
         }}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"CPA Ajustado (c/ imposto): {_fmt_brl(r.get('cpa_ajustado'))}"
         }}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"CPA Meta (plataforma): {_fmt_brl(r.get('cpa_meta'))}"
         }}]}},

        # --- Seção: Alcance & Engajamento ---
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "👁 Alcance & Engajamento"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"Impressões: {_fmt_n(r['total_impressoes'])}  |  CPM: {_fmt_brl(r['cpm_medio'])}"
         }}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"Alcance único: {_fmt_n(r.get('total_alcance'))}  |  "
            f"Frequência média: {_fmt_n(r.get('frequencia_media'), 2)}"
         }}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"Cliques: {_fmt_n(r['total_cliques'])}  |  "
            f"CTR: {_fmt_pct(r['ctr'])}  |  "
            f"CPC: {_fmt_brl(r['cpc_medio'])}"
         }}]}},

        # --- Seção: Conversão ---
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "🛒 Conversão"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"Adições ao carrinho: {_fmt_n(r['total_carrinho'])}"
         }}]}},
    ]

    # Checkouts iniciados com % do carrinho
    if r.get("total_carrinho") and r["total_carrinho"] > 0:
        pct_checkout = round(r["total_checkout"] / r["total_carrinho"] * 100, 1)
        blocos.append({"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content":
                f"Checkouts iniciados: {_fmt_n(r['total_checkout'])}  ({pct_checkout}% do carrinho)"
            }}]}})
    else:
        blocos.append({"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content":
                f"Checkouts iniciados: {_fmt_n(r['total_checkout'])}"
            }}]}})

    # Compras com % do checkout
    if r.get("total_checkout") and r["total_checkout"] > 0:
        pct_compra = round(r["total_compras"] / r["total_checkout"] * 100, 1)
        blocos.append({"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content":
                f"Compras: {r['total_compras']}  ({pct_compra}% do checkout)"
            }}]}})
    else:
        blocos.append({"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content":
                f"Compras: {r['total_compras']}"
            }}]}})

    blocos.append({"object": "block", "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"Taxa clique→compra: {_fmt_pct(taxa_clique_compra)}"
        }}]}})

    # Receita atribuída (cross) se houver
    if r.get("receita_atribuida") and r["receita_atribuida"] > 0:
        blocos.append({"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content":
                f"Receita atribuída (cross): {_fmt_brl(r['receita_atribuida'])}"
            }}]}})

    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


def _blocos_tabela_campanhas(rows: list[dict]) -> list[dict]:
    """Tabela principal de campanhas (8 colunas) + toggle com métricas técnicas (8 col)."""
    if not rows:
        return []

    # ---- Tabela principal: 8 colunas ----
    header_main = ["Campanha", "Invest.", "ROAS", "Compras", "Receita", "CPA Ajust.", "CTR", "CPM"]
    main_rows = [
        {
            "type": "table_row",
            "table_row": {"cells": [[{"text": {"content": h}, "annotations": {"bold": True}}] for h in header_main]}
        }
    ]

    # ---- Tabela técnica: 8 colunas (dentro do toggle) ----
    header_tech = ["Campanha", "CPC", "Frequência", "Impressões", "Cliques", "Carrinho", "Checkout", "Ticket Médio"]
    tech_rows = [
        {
            "type": "table_row",
            "table_row": {"cells": [[{"text": {"content": h}, "annotations": {"bold": True}}] for h in header_tech]}
        }
    ]

    for c in rows:
        invest    = float(c.get("investimento") or 0)
        compras   = int(c.get("compras") or 0)
        receita   = float(c.get("valor_compras") or 0)
        roas      = round(receita / invest, 4) if invest > 0 else None
        cpa_ajust = calcular_cpa(invest, compras)
        cpc       = c.get("cpc_link")
        ctr       = c.get("ctr_link")
        cpm       = c.get("cpm")
        impressoes = int(c.get("impressoes") or 0)
        cliques   = int(c.get("cliques_link") or 0)
        carrinho  = int(c.get("adicoes_carrinho") or 0)
        checkout  = int(c.get("checkout_iniciado") or 0)
        freq      = c.get("frequencia")
        ticket    = round(receita / compras, 2) if compras else None
        nome      = (c.get("campanha") or "")[:60]

        main_rows.append({
            "type": "table_row",
            "table_row": {"cells": [
                [{"text": {"content": nome}}],
                [{"text": {"content": _fmt_brl(invest)}}],
                [{"text": {"content": _fmt_x(roas)}}],
                [{"text": {"content": str(compras) if compras else "—"}}],
                [{"text": {"content": _fmt_brl(receita)}}],
                [{"text": {"content": _fmt_brl(cpa_ajust)}}],
                [{"text": {"content": _fmt_pct(float(ctr)) if ctr else "—"}}],
                [{"text": {"content": _fmt_brl(float(cpm)) if cpm else "—"}}],
            ]}
        })

        tech_rows.append({
            "type": "table_row",
            "table_row": {"cells": [
                [{"text": {"content": nome}}],
                [{"text": {"content": _fmt_brl(float(cpc)) if cpc else "—"}}],
                [{"text": {"content": _fmt_n(float(freq), 2) if freq else "—"}}],
                [{"text": {"content": _fmt_n(impressoes)}}],
                [{"text": {"content": _fmt_n(cliques)}}],
                [{"text": {"content": str(carrinho) if carrinho else "—"}}],
                [{"text": {"content": str(checkout) if checkout else "—"}}],
                [{"text": {"content": _fmt_brl(ticket)}}],
            ]}
        })

    # Toggle encapsulando a tabela técnica
    tabela_tecnica = {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(header_tech),
            "has_column_header": True,
            "has_row_header": False,
            "children": tech_rows,
        }
    }
    toggle_tecnico = {
        "object": "block",
        "type": "toggle",
        "toggle": {
            "rich_text": [{"text": {"content": "📐 Métricas técnicas detalhadas"}}],
            "children": [tabela_tecnica],
        }
    }

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📋 Desempenho por Campanha"}}]}},
        {
            "object": "block",
            "type": "table",
            "table": {
                "table_width": len(header_main),
                "has_column_header": True,
                "has_row_header": False,
                "children": main_rows,
            }
        },
        toggle_tecnico,
        {"object": "block", "type": "divider", "divider": {}},
    ]


def _blocos_top_campanhas(campanhas: list[dict]) -> list[dict]:
    """Top 5 campanhas por ROAS — com receita, compras e investimento."""
    rankeadas = []
    for c in campanhas:
        invest  = float(c.get("investimento") or 0)
        receita = float(c.get("valor_compras") or 0)
        compras = int(c.get("compras") or 0)
        roas    = round(receita / invest, 4) if invest > 0 else None
        if roas:
            rankeadas.append((c.get("campanha", ""), roas, invest, receita, compras))

    rankeadas.sort(key=lambda x: x[1], reverse=True)

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🏆 Top Campanhas por ROAS"}}]}},
    ]
    for i, (nome, roas, invest, receita, compras) in enumerate(rankeadas[:5], 1):
        linha = (
            f"🏆 {nome}  —  "
            f"ROAS {roas:.2f}x  ·  "
            f"Receita {_fmt_brl(receita)}  ·  "
            f"Compras {compras}  ·  "
            f"Invest {_fmt_brl(invest)}"
        )
        blocos.append({
            "object": "block", "type": "numbered_list_item",
            "numbered_list_item": {"rich_text": [{"text": {"content": linha}}]}
        })

    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


def _blocos_alocacao(campanhas: list[dict]) -> list[dict]:
    """Budget allocation — com ROAS por campanha."""
    total = sum(float(c.get("investimento") or 0) for c in campanhas)
    if total == 0:
        return []

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "💰 Alocação de Budget"}}]}},
    ]
    for c in sorted(campanhas, key=lambda x: float(x.get("investimento") or 0), reverse=True):
        invest  = float(c.get("investimento") or 0)
        receita = float(c.get("valor_compras") or 0)
        roas    = round(receita / invest, 2) if invest > 0 else None
        pct     = calcular_pct_budget(invest, total)
        nome    = c.get("campanha", "")

        if roas and roas > 0:
            roas_str = f" — ROAS {roas:.2f}x"
        else:
            roas_str = " — sem conversão"

        blocos.append({
            "object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content":
                f"{nome}  —  {_fmt_brl(invest)}  ({_fmt_pct(pct)}){roas_str}"
            }}]}
        })

    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


def _blocos_alertas(alertas: list[str]) -> list[dict]:
    """Bloco de alertas automáticos com callout colorido baseado na severidade."""
    # Determina cor do callout
    tem_critico   = any("🔴" in a for a in alertas)
    tem_atencao   = any(("🟡" in a or "⚠️" in a) for a in alertas)
    so_ok         = len(alertas) == 1 and alertas[0].startswith("✅")

    if so_ok:
        callout_color = "green_background"
        callout_emoji = "✅"
    elif tem_critico:
        callout_color = "red_background"
        callout_emoji = "🚨"
    elif tem_atencao:
        callout_color = "yellow_background"
        callout_emoji = "⚠️"
    else:
        callout_color = "gray_background"
        callout_emoji = "💡"

    alertas_txt = "\n".join(alertas)
    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🚨 Alertas Automáticos"}}]}},
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": alertas_txt}}],
                "icon": {"type": "emoji", "emoji": callout_emoji},
                "color": callout_color,
            }
        },
    ]
    return blocos




def _blocos_comparativo(resumo_atual: dict, resumo_anterior: dict, periodo_anterior_str: str) -> list[dict]:
    """
    Bloco de comparativo entre período atual e anterior.
    Destaque visual correto por direção: métricas onde MENOR é melhor
    (CPA, CPC, CPM) têm lógica de cor invertida.
    """
    # Métricas onde uma queda é POSITIVA (menor = melhor)
    MENOR_MELHOR = {"CPA", "CPC", "CPM"}

    def _variacao_pct(atual, anterior, nome_metrica):
        if atual is None or anterior is None or anterior == 0:
            return None, "→ Estável"
        var = (atual - anterior) / abs(anterior) * 100
        if abs(var) < 5:
            return var, "→ Estável"
        # Inverte lógica para métricas onde menor é melhor
        subiu = var > 0
        if nome_metrica in MENOR_MELHOR:
            tendencia = "⬇️ Melhorou" if not subiu else "⬆️ Piorou"
        else:
            tendencia = "⬆️ Melhorou" if subiu else "⬇️ Piorou"
        return var, tendencia

    def _fmt_var(var):
        if var is None:
            return "—"
        sign = "+" if var > 0 else ""
        return f"{sign}{var:.1f}%"

    metricas = [
        ("ROAS",     resumo_atual.get("roas"),          resumo_anterior.get("roas"),          _fmt_x),
        ("Invest.",  resumo_atual.get("total_invest"),   resumo_anterior.get("total_invest"),  _fmt_brl),
        ("Receita",  resumo_atual.get("total_receita"),  resumo_anterior.get("total_receita"), _fmt_brl),
        ("Compras",  resumo_atual.get("total_compras"),  resumo_anterior.get("total_compras"), lambda v: str(int(v)) if v is not None else "—"),
        ("CPA",      resumo_atual.get("cpa"),            resumo_anterior.get("cpa"),           _fmt_brl),
        ("CTR",      resumo_atual.get("ctr"),            resumo_anterior.get("ctr"),           _fmt_pct),
        ("CPC",      resumo_atual.get("cpc_medio"),      resumo_anterior.get("cpc_medio"),     _fmt_brl),
        ("CPM",      resumo_atual.get("cpm_medio"),      resumo_anterior.get("cpm_medio"),     _fmt_brl),
    ]

    receita_atrib = resumo_atual.get("receita_atribuida", 0) or 0
    if receita_atrib > 0:
        metricas.append((
            "Receita Atribuída (cross)",
            receita_atrib,
            resumo_anterior.get("receita_atribuida", 0) or 0,
            _fmt_brl,
        ))

    header = ["Métrica", "Atual", "Anterior", "Variação", "Tendência"]
    table_rows = [
        {
            "type": "table_row",
            "table_row": {"cells": [[{"text": {"content": h}, "annotations": {"bold": True}}] for h in header]}
        }
    ]

    for nome, atual, anterior, fmt_fn in metricas:
        var, tend = _variacao_pct(atual, anterior, nome)
        table_rows.append({
            "type": "table_row",
            "table_row": {"cells": [
                [{"text": {"content": nome}}],
                [{"text": {"content": fmt_fn(atual)}}],
                [{"text": {"content": fmt_fn(anterior)}}],
                [{"text": {"content": _fmt_var(var)}}],
                [{"text": {"content": tend}}],
            ]}
        })

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": f"📈 Comparativo com Período Anterior ({periodo_anterior_str})"}}]}},
        {
            "object": "block",
            "type": "table",
            "table": {
                "table_width": len(header),
                "has_column_header": True,
                "has_row_header": False,
                "children": table_rows,
            }
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]


def _blocos_funil(resumo: dict) -> list[dict]:
    """
    Funil de conversão visual com quebra correta:
    Alcance → Impressões (frequência) → Cliques (CTR) → Carrinho → Checkout → Compras.
    Separa alcance de impressões para não misturar frequência com taxa de clique.
    """
    r = resumo
    impressoes = r.get("total_impressoes") or 0
    alcance    = r.get("total_alcance") or 0
    cliques    = r.get("total_cliques") or 0
    carrinho   = r.get("total_carrinho") or 0
    checkout   = r.get("total_checkout") or 0
    compras    = r.get("total_compras") or 0
    ctr        = r.get("ctr")
    frequencia = r.get("frequencia_media")

    # Taxas entre etapas
    pct_impr_clique      = round(cliques  / impressoes * 100, 2) if impressoes else None  # = CTR
    pct_alcance_impr     = round(impressoes / alcance, 2) if alcance else None             # = frequência
    pct_clique_carrinho  = round(carrinho / cliques  * 100, 1) if cliques   else None
    pct_carrinho_checkout= round(checkout / carrinho * 100, 1) if carrinho  else None
    pct_checkout_compra  = round(compras  / checkout * 100, 1) if checkout  else None
    efic_geral           = round(compras  / impressoes * 100, 3) if impressoes else None

    def _p(txt):
        return {
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": txt}}]}
        }

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🔽 Funil de Conversão"}}]}},
    ]

    # Etapa 1: Alcance (pessoas únicas)
    blocos.append(_p(f"👤 {_fmt_n(alcance)} pessoas alcançadas (alcance único)"))

    # Etapa 2: Impressões (frequência = impr/alcance)
    freq_str = f" — frequência média {_fmt_n(float(frequencia), 2)}x" if frequencia else ""
    blocos.append(_p(f"   ↳ {_fmt_n(impressoes)} impressões{freq_str}"))

    # Etapa 3: Cliques (CTR = cliques/impressões)
    ctr_str = f" — CTR {_fmt_pct(pct_impr_clique)}" if pct_impr_clique else ""
    blocos.append(_p(f"   ↳ {_fmt_n(cliques)} cliques no link{ctr_str}"))

    # Etapa 4: Carrinho
    carr_str = f" — {_fmt_pct(pct_clique_carrinho)} dos cliques" if pct_clique_carrinho else ""
    blocos.append(_p(f"   ↳ {_fmt_n(carrinho)} adições ao carrinho{carr_str}"))

    # Etapa 5: Checkout
    chk_str = f" — {_fmt_pct(pct_carrinho_checkout)} do carrinho" if pct_carrinho_checkout else ""
    blocos.append(_p(f"   ↳ {_fmt_n(checkout)} checkouts iniciados{chk_str}"))

    # Etapa 6: Compras
    cmp_str = f" — {_fmt_pct(pct_checkout_compra)} do checkout" if pct_checkout_compra else ""
    blocos.append(_p(f"   ↳ {_fmt_n(compras)} compras{cmp_str}"))

    # Callout com eficiência geral
    efic_txt = (
        f"Eficiência geral alcance→compra: {efic_geral:.3f}% "
        f"({compras} compras para {_fmt_n(alcance)} pessoas alcançadas)"
        if efic_geral is not None
        else "Eficiência geral: —"
    )
    blocos.append({
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": efic_txt}}],
            "icon": {"type": "emoji", "emoji": "🔽"},
        }
    })
    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


# ── Novas funções para relatório mensal ─────────────────────────────────────


def _blocos_analise_ultima_semana(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Seção 1 — Análise da Última Semana.
    Busca dados das 2 últimas semanas do mês, gera texto narrativo
    e tabela comparativa semanal.
    """
    from calendar import monthrange

    # Semana 2 (última): últimos 7 dias do mês
    sem2_fim   = data_fim
    sem2_ini   = data_fim - timedelta(days=6)
    # Semana 1 (penúltima): 7 dias antes da última semana
    sem1_fim   = sem2_ini - timedelta(days=1)
    sem1_ini   = sem1_fim - timedelta(days=6)

    def _buscar_semana(ini: date, fim: date):
        conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT
                SUM(investimento)               AS invest,
                SUM(COALESCE(compras, 0))        AS compras,
                SUM(COALESCE(valor_compras, 0))  AS receita
            FROM campanhas
            WHERE data_inicio >= ? AND data_fim <= ?
        """, (ini.isoformat(), fim.isoformat()))
        row = cur.fetchone()
        conn.close()
        invest  = float(row["invest"] or 0)
        compras = int(row["compras"] or 0)
        receita = float(row["receita"] or 0)
        roas    = round(receita / invest, 2) if invest > 0 else None
        ticket  = round(receita / compras, 2) if compras else None
        invest_imp = spend_com_imposto(invest)
        return dict(
            invest=invest, invest_imp=invest_imp,
            compras=compras, receita=receita,
            roas=roas, ticket=ticket,
        )

    s1 = _buscar_semana(sem1_ini, sem1_fim)
    s2 = _buscar_semana(sem2_ini, sem2_fim)

    # Texto narrativo da última semana
    def _comp(atual, anterior, nome, fmt_fn):
        if anterior and anterior != 0:
            var = (atual - anterior) / abs(anterior) * 100
            sinal = "acima" if var > 0 else "abaixo"
            return f"{fmt_fn(atual)} ({abs(var):.1f}% {sinal} da semana anterior)"
        return fmt_fn(atual)

    narrativa_partes = []
    narrativa_partes.append(
        f"Na última semana do mês ({sem2_ini.strftime('%d/%m')}–{sem2_fim.strftime('%d/%m')}), "
        f"o investimento em Meta Ads foi de {_comp(s2['invest'], s1['invest'], 'investimento', _fmt_brl)}, "
        f"com ROAS de {_fmt_x(s2['roas'])}"
    )
    if s1['roas'] and s2['roas']:
        var_roas = (s2['roas'] - s1['roas']) / abs(s1['roas']) * 100 if s1['roas'] else 0
        tend = "acima" if var_roas > 0 else "abaixo"
        narrativa_partes[-1] += f" ({abs(var_roas):.1f}% {tend} da semana anterior)"
    narrativa_partes[-1] += "."

    narrativa_partes.append(
        f"Foram registradas {s2['compras']} transações, "
        f"com receita de {_comp(s2['receita'], s1['receita'], 'receita', _fmt_brl)}. "
        f"Ticket médio: {_fmt_brl(s2['ticket'])}."
    )

    narrativa = " ".join(narrativa_partes)

    # Tabela comparativa
    header = ["Semana", "Invest. Meta", "Invest.+Imposto", "Transações", "Receita", "ROAS", "Ticket Médio"]
    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}}
    ]

    for label, ini, fim, s in [
        (f"Sem. {sem1_ini.strftime('%d/%m')}–{sem1_fim.strftime('%d/%m')}", sem1_ini, sem1_fim, s1),
        (f"Sem. {sem2_ini.strftime('%d/%m')}–{sem2_fim.strftime('%d/%m')}", sem2_ini, sem2_fim, s2),
    ]:
        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": label}}],
            [{"text": {"content": _fmt_brl(s["invest"])}}],
            [{"text": {"content": _fmt_brl(s["invest_imp"])}}],
            [{"text": {"content": str(s["compras"]) if s["compras"] else "—"}}],
            [{"text": {"content": _fmt_brl(s["receita"])}}],
            [{"text": {"content": _fmt_x(s["roas"])}}],
            [{"text": {"content": _fmt_brl(s["ticket"])}}],
        ]}})

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📝 Análise da Última Semana"}}]}},
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": narrativa}}],
                "icon": {"type": "emoji", "emoji": "📝"},
                "color": "blue_background",
            }
        },
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Semanal"}}]}},
        {
            "object": "block", "type": "table", "table": {
                "table_width": len(header),
                "has_column_header": True,
                "has_row_header": False,
                "children": table_rows,
            }
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]


def _blocos_visao_mensal_diaria(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Seção 2 — Visão Mensal (tabela dia a dia).
    Agrega campanhas por dia e retorna tabela Notion com uma linha por dia + total.
    """
    conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            data_inicio                        AS dia,
            SUM(investimento)                  AS invest,
            SUM(COALESCE(compras, 0))           AS compras,
            SUM(COALESCE(valor_compras, 0))     AS receita
        FROM campanhas
        WHERE data_inicio >= ? AND data_fim <= ?
        GROUP BY data_inicio
        ORDER BY data_inicio
    """, (data_inicio.isoformat(), data_fim.isoformat()))
    rows = cur.fetchall()
    conn.close()

    header = ["Data", "Gasto", "Compras Meta", "Receita Meta", "ROAS Meta", "Ticket Médio Meta"]
    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}}
    ]

    total_invest = 0.0
    total_compras = 0
    total_receita = 0.0

    for row in rows:
        invest  = float(row["invest"] or 0)
        compras = int(row["compras"] or 0)
        receita = float(row["receita"] or 0)
        roas    = round(receita / invest, 2) if invest > 0 else None
        ticket  = round(receita / compras, 2) if compras else None

        # Formatar data como dd/mm
        try:
            dia_date = date.fromisoformat(row["dia"])
            dia_str  = dia_date.strftime("%d/%m")
        except Exception:
            dia_str = str(row["dia"])

        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": dia_str}}],
            [{"text": {"content": _fmt_brl(invest)}}],
            [{"text": {"content": str(compras) if compras else "—"}}],
            [{"text": {"content": _fmt_brl(receita)}}],
            [{"text": {"content": _fmt_x(roas)}}],
            [{"text": {"content": _fmt_brl(ticket)}}],
        ]}})

        total_invest  += invest
        total_compras += compras
        total_receita += receita

    # Se não houver dados, pelo menos 1 linha de dados (regra Notion)
    if not rows:
        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": "—"}}],
            [{"text": {"content": "—"}}],
            [{"text": {"content": "—"}}],
            [{"text": {"content": "—"}}],
            [{"text": {"content": "—"}}],
            [{"text": {"content": "—"}}],
        ]}})

    # Linha de total
    roas_total   = round(total_receita / total_invest, 2) if total_invest > 0 else None
    ticket_total = round(total_receita / total_compras, 2) if total_compras else None
    table_rows.append({"type": "table_row", "table_row": {"cells": [
        [{"text": {"content": "TOTAL"}, "annotations": {"bold": True}}],
        [{"text": {"content": _fmt_brl(total_invest)}, "annotations": {"bold": True}}],
        [{"text": {"content": str(total_compras) if total_compras else "—"}, "annotations": {"bold": True}}],
        [{"text": {"content": _fmt_brl(total_receita)}, "annotations": {"bold": True}}],
        [{"text": {"content": _fmt_x(roas_total)}, "annotations": {"bold": True}}],
        [{"text": {"content": _fmt_brl(ticket_total)}, "annotations": {"bold": True}}],
    ]}})

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📊 Visão Mensal"}}]}},
        {
            "object": "block", "type": "table", "table": {
                "table_width": len(header),
                "has_column_header": True,
                "has_row_header": False,
                "children": table_rows,
            }
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]


def _blocos_alocacao_canais(resumo: dict) -> list[dict]:
    """
    Seção 3 — Alocação Budget por Canal.
    Meta Ads com valor real + Google Ads como placeholder.
    """
    header = ["Canal", "Investimento"]
    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}},
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": "Meta Ads"}}],
            [{"text": {"content": _fmt_brl(resumo.get("total_invest"))}}],
        ]}},
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": "Google Ads"}}],
            [{"text": {"content": "— (integração pendente)"}}],
        ]}},
    ]

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "💰 Alocação Budget Canais"}}]}},
        {
            "object": "block", "type": "table", "table": {
                "table_width": len(header),
                "has_column_header": True,
                "has_row_header": False,
                "children": table_rows,
            }
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]


def _blocos_alocacao_por_anuncio(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Seção 4 — Alocação Budget por Anúncio (Meta Ads).
    Agrega ads_diario por ad_name, ordena por investimento DESC.
    """
    conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            ad_name,
            SUM(investimento)               AS invest,
            SUM(COALESCE(compras, 0))        AS compras,
            SUM(COALESCE(valor_compras, 0))  AS receita,
            MAX(status_criativo)             AS status
        FROM ads_diario
        WHERE data_inicio >= ? AND data_inicio <= ?
        GROUP BY ad_name
        HAVING SUM(investimento) > 0
        ORDER BY SUM(investimento) DESC
    """, (data_inicio.isoformat(), data_fim.isoformat()))
    rows = cur.fetchall()
    conn.close()

    header = ["Anúncio", "Invest.", "Compras", "ROAS", "Status Criativo"]
    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}}
    ]

    for row in rows:
        invest  = float(row["invest"] or 0)
        compras = int(row["compras"] or 0)
        receita = float(row["receita"] or 0)
        roas    = round(receita / invest, 2) if invest > 0 else None
        nome    = (row["ad_name"] or "")[:80]
        status  = row["status"] or "—"

        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": nome}}],
            [{"text": {"content": _fmt_brl(invest)}}],
            [{"text": {"content": str(compras) if compras else "—"}}],
            [{"text": {"content": _fmt_x(roas)}}],
            [{"text": {"content": status}}],
        ]}})

    # Garantir ao menos 1 linha de dados além do header (regra Notion)
    if not rows:
        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": "—"}}],
            [{"text": {"content": "—"}}],
            [{"text": {"content": "—"}}],
            [{"text": {"content": "—"}}],
            [{"text": {"content": "—"}}],
        ]}})

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🎯 Alocação Budget por Anúncio (Meta Ads)"}}]}},
        {
            "object": "block", "type": "table", "table": {
                "table_width": len(header),
                "has_column_header": True,
                "has_row_header": False,
                "children": table_rows,
            }
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]


def _blocos_placeholder(
    titulo: str,
    emoji: str,
    descricao: str,
    colunas: list[str] = None,
) -> list[dict]:
    """
    Seção genérica para dados ainda não disponíveis.
    Retorna heading_2 + callout amarelo + (opcional) tabela vazia com header + 1 linha —.
    """
    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": f"{emoji} {titulo}"}}]}},
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": descricao}}],
                "icon": {"type": "emoji", "emoji": "⏳"},
                "color": "yellow_background",
            }
        },
    ]

    if colunas:
        header_row = {
            "type": "table_row",
            "table_row": {"cells": [
                [{"text": {"content": col}, "annotations": {"bold": True}}]
                for col in colunas
            ]}
        }
        # Linha vazia obrigatória (Notion exige pelo menos 1 linha de dados)
        data_row = {
            "type": "table_row",
            "table_row": {"cells": [
                [{"text": {"content": "—"}}] for _ in colunas
            ]}
        }
        blocos.append({
            "object": "block", "type": "table", "table": {
                "table_width": len(colunas),
                "has_column_header": True,
                "has_row_header": False,
                "children": [header_row, data_row],
            }
        })

    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


def _blocos_metas_mes(resumo: dict) -> list[dict]:
    """
    Seção 13 — Onde Estamos — Metas do Mês.
    Tabela: Métrica | Realizado | Meta | Variação.
    Meta e Variação como placeholder até integração de metas.
    """
    r = resumo
    taxa_conv = round(
        r["total_compras"] / r["total_cliques"] * 100, 2
    ) if r.get("total_cliques") and r["total_compras"] else None

    metricas = [
        ("Receita mensal",      _fmt_brl(r.get("total_receita"))),
        ("ROAS",                _fmt_x(r.get("roas"))),
        ("Ticket médio",        _fmt_brl(r.get("ticket_medio"))),
        ("Taxa de conversão",   _fmt_pct(taxa_conv)),
        ("Compras/mês",         str(r.get("total_compras", 0))),
    ]

    header = ["Métrica", "Realizado", "Meta", "Variação"]
    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}}
    ]
    for nome, realizado in metricas:
        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": nome}}],
            [{"text": {"content": realizado}}],
            [{"text": {"content": "—"}}],
            [{"text": {"content": "—"}}],
        ]}})

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🎯 Onde Estamos"}}]}},
        {
            "object": "block", "type": "table", "table": {
                "table_width": len(header),
                "has_column_header": True,
                "has_row_header": False,
                "children": table_rows,
            }
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]


def _blocos_planejamento_criativo(data_fim: date) -> list[dict]:
    """
    Seção 15 — Planejamento Criativo (próximos 3 meses).
    Heading + 3 heading_3 com meses em português + callout placeholder em cada um.
    """
    meses_futuros = []
    ano, mes = data_fim.year, data_fim.month
    for _ in range(3):
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
        meses_futuros.append(f"{_MESES_PT[mes]}/{ano}")

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📅 Planejamento Criativo"}}]}},
    ]
    for mes_label in meses_futuros:
        blocos.append({
            "object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"text": {"content": mes_label}}]}
        })
        blocos.append({
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "⏳ Planejamento criativo a ser preenchido pela equipe Guará."}}],
                "icon": {"type": "emoji", "emoji": "📅"},
                "color": "yellow_background",
            }
        })
    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


def _blocos_proximos_passos() -> list[dict]:
    """
    Seção 16 — Próximos Passos.
    Heading + heading_3 'Guará Marketing' e 'Source.Of' + 3 bullets cada.
    """
    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "✅ Próximos Passos"}}]}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Guará Marketing"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "[ ] A definir"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "[ ] A definir"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "[ ] A definir"}}]}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Source.Of"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "[ ] A definir"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "[ ] A definir"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "[ ] A definir"}}]}},
        {"object": "block", "type": "divider", "divider": {}},
    ]


# ── Função principal ──────────────────────────────────────────────────────────


def _blocos_tendencia_semanal(semanas: int = 4) -> list[dict]:
    """
    Tendencia das ultimas N semanas (WoW) no relatorio Notion.
    """
    try:
        from agent.comparatives import comparar_semana_a_semana
        serie = comparar_semana_a_semana(semanas=semanas)
    except Exception as e:
        logger.warning("[Builder] _blocos_tendencia_semanal falhou: %s", e)
        return []
    if not serie:
        return []
    header = ["Semana", "Invest.", "Receita", "ROAS", "Compras", "CTR", "CPC", "D ROAS", "D Receita"]
    def _fmt_wow(v):
        if v is None: return ""
        sign = "+" if v > 0 else ""
        arrow = "UP" if v > 5 else ("DOWN" if v < -5 else "->")
        return f"{arrow} {sign}{v:.1f}%"
    table_rows = [{"type":"table_row","table_row":{"cells":[[{"text":{"content":h},"annotations":{"bold":True}}] for h in header]}}]
    for s in serie:
        table_rows.append({"type":"table_row","table_row":{"cells":[
            [{"text":{"content":s.get("semana","")}}],
            [{"text":{"content":_fmt_brl(s.get("investimento"))}}],
            [{"text":{"content":_fmt_brl(s.get("receita"))}}],
            [{"text":{"content":_fmt_x(s.get("roas"))}}],
            [{"text":{"content":_fmt_n(s.get("compras"))}}],
            [{"text":{"content":_fmt_pct(s.get("ctr"))}}],
            [{"text":{"content":_fmt_brl(s.get("cpc"))}}],
            [{"text":{"content":_fmt_wow(s.get("roas_wow_pct"))}}],
            [{"text":{"content":_fmt_wow(s.get("receita_wow_pct"))}}],
        ]}})
    return [
        {"object":"block","type":"heading_2","heading_2":{"rich_text":[{"text":{"content":f"Tendencia Ultimas {semanas} Semanas (WoW)"}}]}},
        {"object":"block","type":"table","table":{"table_width":len(header),"has_column_header":True,"has_row_header":False,"children":table_rows}},
        {"object":"block","type":"divider","divider":{}},
    ]


_MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março",    4: "Abril",
    5: "Maio",    6: "Junho",     7: "Julho",    8: "Agosto",
    9: "Setembro",10: "Outubro",  11: "Novembro", 12: "Dezembro",
}


def _blocos_tendencia_mensal(data_ref: date, meses: int = 4, db_path: Path = DB_PATH) -> list[dict]:
    """
    Tendência dos últimos N meses completos antes de data_ref (MoM).
    Usa tabela Notion com colunas: Mês | Invest. | ROAS | Compras | Receita.
    Dados calculados direto do SQLite agregando por mês.
    """
    from calendar import monthrange

    # Monta lista de meses: os `meses` meses anteriores ao mês de data_ref
    registros = []
    ano, mes = data_ref.year, data_ref.month
    for _ in range(meses):
        mes -= 1
        if mes == 0:
            mes = 12
            ano -= 1
        _, ultimo_dia = monthrange(ano, mes)
        ini = date(ano, mes, 1)
        fim = date(ano, mes, ultimo_dia)
        registros.append((ano, mes, ini, fim))

    registros.reverse()  # ordem cronológica

    # Busca dados para cada mês no SQLite
    conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    serie = []
    for ano_m, mes_m, ini, fim in registros:
        cur.execute("""
            SELECT
                SUM(investimento)         AS invest,
                SUM(COALESCE(compras,0))  AS compras,
                SUM(COALESCE(valor_compras,0)) AS receita
            FROM campanhas
            WHERE data_inicio >= ? AND data_fim <= ?
        """, (ini.isoformat(), fim.isoformat()))
        row = cur.fetchone()
        invest  = float(row["invest"] or 0)
        compras = int(row["compras"] or 0)
        receita = float(row["receita"] or 0)
        roas    = round(receita / invest, 2) if invest > 0 else None
        serie.append({
            "mes_label": f"{_MESES_PT[mes_m]}/{ano_m}",
            "invest":    invest,
            "roas":      roas,
            "compras":   compras,
            "receita":   receita,
        })
    conn.close()

    if not any(s["invest"] > 0 for s in serie):
        return []

    header = ["Mês", "Invest.", "ROAS", "Compras", "Receita"]
    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}}
    ]
    for s in serie:
        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": s["mes_label"]}}],
            [{"text": {"content": _fmt_brl(s["invest"])}}],
            [{"text": {"content": _fmt_x(s["roas"])}}],
            [{"text": {"content": str(s["compras"]) if s["compras"] else "—"}}],
            [{"text": {"content": _fmt_brl(s["receita"])}}],
        ]}})

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": f"📅 Tendência — Últimos {meses} Meses"}}]}},
        {"object": "block", "type": "table", "table": {
            "table_width": len(header),
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows,
        }},
        {"object": "block", "type": "divider", "divider": {}},
    ]


def gerar_relatorio_semanal(
    cliente: str = "source_of",
    data_inicio: date = None,
    data_fim: date = None,
    publicar: bool = True,
    db_path: Path = DB_PATH,
    tipo: str = "semanal",
) -> dict:
    """
    Gera e publica o relatório semanal (ou mensal) Meta Ads.

    Args:
        cliente:      chave do cliente (ex: 'source_of')
        data_inicio:  início do período (default: segunda-feira da semana passada)
        data_fim:     fim do período (default: domingo da semana passada)
        publicar:     se True, publica no Notion
        db_path:      caminho do SQLite
        tipo:         "semanal" (padrão) ou "mensal" — altera comparativo, tendência,
                      alocação e título

    Returns:
        dict com: page_id, url, resumo, num_campanhas, alertas
    """
    # ── Período ───────────────────────────────────────────────────────────
    if data_inicio is None or data_fim is None:
        hoje    = date.today()
        seg_ant = hoje - timedelta(days=hoje.weekday() + 7)
        dom_ant = seg_ant + timedelta(days=6)
        data_inicio = data_inicio or seg_ant
        data_fim    = data_fim    or dom_ant

    cliente_nome = CLIENTES.get(cliente, cliente)

    # ── Título e período anterior ─────────────────────────────────────────
    if tipo == "mensal":
        # Título: "📊 Source OF · Meta Ads Mensal · Junho/2026"
        mes_pt = _MESES_PT[data_inicio.month]
        periodo_str = f"{mes_pt}/{data_inicio.year}"
        titulo_periodo = periodo_str

        # Período anterior = mês anterior completo
        primeiro_atual = data_inicio  # já é dia 1
        ant_fim = primeiro_atual - timedelta(days=1)       # último dia do mês anterior
        ant_inicio = ant_fim.replace(day=1)                # primeiro dia do mês anterior
        periodo_anterior_str = f"{_MESES_PT[ant_inicio.month]}/{ant_inicio.year}"
    else:
        periodo_str = f"{data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"
        titulo_periodo = periodo_str

        # Período anterior: mesma duração
        delta_dias = (data_fim - data_inicio).days + 1
        ant_fim    = data_inicio - timedelta(days=1)
        ant_inicio = ant_fim - timedelta(days=delta_dias - 1)
        periodo_anterior_str = f"{ant_inicio.strftime('%d/%m/%Y')} → {ant_fim.strftime('%d/%m/%Y')}"

    logger.info("[Builder] Gerando relatório %s · %s · tipo=%s", cliente_nome, periodo_str, tipo)

    # ── Validação dos dados ──────────────────────────────────────────────────
    try:
        from db.validator import validar
        val_report = validar(db_path)
        if not val_report.ok:
            logger.error("[Builder] Dados com ERRORs de validação — relatório pode conter inconsistências")
        elif val_report.warnings:
            logger.warning("[Builder] Dados com %d aviso(s) de validação", len(val_report.warnings))
    except Exception as e:
        logger.warning("[Builder] Falha na validação pré-relatório: %s", e)
        val_report = None

    # ── Dados ─────────────────────────────────────────────────────────────
    campanhas = _buscar_campanhas(data_inicio, data_fim, db_path)
    if not campanhas:
        logger.warning("[Builder] Nenhuma campanha encontrada no período %s", periodo_str)
        return {"erro": "Nenhuma campanha encontrada", "periodo": periodo_str}

    resumo  = _resumo_geral(campanhas)

    # ── Dados período anterior ─────────────────────────────────────────────
    campanhas_anterior = _buscar_campanhas(ant_inicio, ant_fim, db_path)
    resumo_anterior    = _resumo_geral(campanhas_anterior) if campanhas_anterior else {}

    alertas = _alertas(campanhas, resumo, resumo_anterior=resumo_anterior)

    logger.info("[Builder] %d campanhas · Invest.: %s · ROAS: %s",
                len(campanhas), _fmt_brl(resumo["total_invest"]), _fmt_x(resumo["roas"]))

    # ── Blocos Notion ──────────────────────────────────────────────────────
    if tipo == "mensal":
        titulo = f"📊 {cliente_nome} · Meta Ads Mensal · {titulo_periodo}"
    else:
        titulo = f"📊 {cliente_nome} · Meta Ads · {titulo_periodo}"

    # Callout de geração automática
    callout_gerado = {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content":
                f"Relatório gerado automaticamente pelo Agente de Performance Guará · {periodo_str}"
            }}],
            "icon": {"type": "emoji", "emoji": "⚙️"},
        }
    }

    if tipo == "mensal":
        # ── Estrutura completa do relatório mensal (17 seções) ──────────────
        blocos = [callout_gerado]

        # 1. Análise da última semana
        blocos += _blocos_analise_ultima_semana(data_inicio, data_fim, db_path)

        # 2. Visão mensal dia a dia
        blocos += _blocos_visao_mensal_diaria(data_inicio, data_fim, db_path)

        # 3. Alocação budget canais
        blocos += _blocos_alocacao_canais(resumo)

        # 4. Alocação budget por anúncio
        blocos += _blocos_alocacao_por_anuncio(data_inicio, data_fim, db_path)

        # 5. Pedidos por SKU (placeholder)
        blocos += _blocos_placeholder(
            "Pedidos por SKU", "📦",
            "⏳ Integração Shopify pendente — dados serão preenchidos automaticamente quando conectado.",
            colunas=["SKU", "Pedidos", "Receita"],
        )

        # 6. Pedidos por Estado (placeholder)
        blocos += _blocos_placeholder(
            "Pedidos por Estado", "🗺️",
            "⏳ Integração Shopify pendente.",
            colunas=["Estado", "Pedidos"],
        )

        # 7. Recuperação de Carrinho (placeholder)
        blocos += _blocos_placeholder(
            "Recuperação de Carrinho Abandonado", "🔄",
            "⏳ Dados de recuperação de carrinho pendentes (fonte: Shopify/e-mail marketing).",
        )

        # 8. Pedidos Orgânicos Instagram (placeholder)
        blocos += _blocos_placeholder(
            "Pedidos Orgânicos Instagram", "📸",
            "⏳ Dados de pedidos orgânicos pendentes (fonte: UTM Instagram).",
        )

        # 9. Resumo executivo
        blocos += _blocos_resumo(resumo, periodo_str)

        # Aviso de qualidade de dados (se houver issues)
        if val_report and (not val_report.ok or val_report.warnings):
            issues_txt = "\n".join(
                f"{'❌' if i.severity == 'ERROR' else '⚠️'} {i.check}: {i.message}"
                for i in val_report.issues
            )
            blocos.append({
                "object": "block", "type": "callout",
                "callout": {
                    "rich_text": [{"text": {"content": f"⚠️ Qualidade dos dados\n{issues_txt}"}}],
                    "icon": {"type": "emoji", "emoji": "⚠️"},
                    "color": "yellow_background",
                }
            })

        # 10. Comparativo mês anterior
        if resumo_anterior:
            blocos += _blocos_comparativo(resumo, resumo_anterior, periodo_anterior_str)

        # 11. Tendência mensal (MoM)
        blocos += _blocos_tendencia_mensal(data_inicio, meses=4, db_path=db_path)

        # 12. Desempenho por campanha
        blocos += _blocos_tabela_campanhas(campanhas)

        # 13. Top campanhas por ROAS
        blocos += _blocos_top_campanhas(campanhas)

        # 14. Onde estamos — Metas do mês
        blocos += _blocos_metas_mes(resumo)

        # 15. Base de Recompradoras (placeholder)
        blocos += _blocos_placeholder(
            "Base de Recompradoras", "👥",
            "⏳ Dados de recompra pendentes (fonte: Shopify/CRM).",
            colunas=["Mês", "Taxa de Recompra", "Volume de Recompras"],
        )

        # 16. Planejamento Criativo
        blocos += _blocos_planejamento_criativo(data_fim)

        # 17. Próximos Passos
        blocos += _blocos_proximos_passos()

        # 18. Funil de conversão
        blocos += _blocos_funil(resumo)

        # 19. Alertas automáticos
        blocos += _blocos_alertas(alertas)

    else:
        # ── Estrutura do relatório semanal (mantida igual) ──────────────────
        blocos = []
        blocos += _blocos_resumo(resumo, periodo_str)
        # Aviso de qualidade de dados (se houver issues)
        if val_report and (not val_report.ok or val_report.warnings):
            issues_txt = "\n".join(
                f"{'❌' if i.severity == 'ERROR' else '⚠️'} {i.check}: {i.message}"
                for i in val_report.issues
            )
            blocos.append({
                "object": "block", "type": "callout",
                "callout": {
                    "rich_text": [{"text": {"content": f"⚠️ Qualidade dos dados\n{issues_txt}"}}],
                    "icon": {"type": "emoji", "emoji": "⚠️"},
                    "color": "yellow_background",
                }
            })
        if resumo_anterior:
            blocos += _blocos_comparativo(resumo, resumo_anterior, periodo_anterior_str)
        blocos += _blocos_tendencia_semanal()
        blocos += _blocos_tabela_campanhas(campanhas)
        blocos += _blocos_top_campanhas(campanhas)
        blocos += _blocos_alocacao(campanhas)
        blocos += _blocos_funil(resumo)
        blocos += _blocos_alertas(alertas)

    # ── Publicação ─────────────────────────────────────────────────────────
    if not publicar:
        logger.info("[Builder] publicar=False — relatório gerado em memória")
        return {
            "titulo": titulo,
            "periodo": periodo_str,
            "resumo": resumo,
            "alertas": alertas,
            "num_campanhas": len(campanhas),
            "blocos": blocos,
        }

    resultado = criar_relatorio_no_historico(
        titulo=titulo,
        blocos=blocos,
        periodo_str=periodo_str,
        tipo=tipo,
        data_ref=data_fim,
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente,
    )

    return {
        "titulo":        titulo,
        "periodo":       periodo_str,
        "page_id":       resultado.get("page_id"),
        "url":           resultado.get("url"),
        "resumo":        resumo,
        "alertas":       alertas,
        "num_campanhas": len(campanhas),
    }


def gerar_relatorio_mensal(
    cliente: str = "source_of",
    ano: int = None,
    mes: int = None,
    publicar: bool = True,
    db_path: Path = DB_PATH,
) -> dict:
    """
    Gera e publica o relatório mensal Meta Ads.

    Args:
        cliente:  chave do cliente (ex: 'source_of')
        ano:      ano do mês de referência (default: mês passado)
        mes:      mês de referência, 1–12 (default: mês passado)
        publicar: se True, publica no Notion
        db_path:  caminho do SQLite

    Returns:
        dict com: page_id, url, resumo, num_campanhas, alertas
    """
    from calendar import monthrange

    hoje = date.today()

    if ano is None or mes is None:
        # Padrão: mês passado completo
        primeiro_este = hoje.replace(day=1)
        ultimo_mes_passado = primeiro_este - timedelta(days=1)
        ano = ano or ultimo_mes_passado.year
        mes = mes or ultimo_mes_passado.month

    _, ultimo_dia = monthrange(ano, mes)
    data_inicio = date(ano, mes, 1)
    data_fim    = date(ano, mes, ultimo_dia)

    return gerar_relatorio_semanal(
        cliente=cliente,
        data_inicio=data_inicio,
        data_fim=data_fim,
        publicar=publicar,
        db_path=db_path,
        tipo="mensal",
    )


def gerar_relatorio_periodo(
    cliente: str = "source_of",
    data_inicio: date = None,
    data_fim: date = None,
    publicar: bool = True,
) -> dict:
    """Alias genérico para períodos customizados."""
    return gerar_relatorio_semanal(
        cliente=cliente,
        data_inicio=data_inicio,
        data_fim=data_fim,
        publicar=publicar,
    )
