"""
relatorio/builder_shared.py

Funções e constantes compartilhadas entre builder_semanal, builder_mensal e builder_periodo.
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

    # Legenda do Resumo Executivo
    blocos.append({
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content":
            "📌 Legenda: ROAS = Receita ÷ Investimento Meta · "
            "CPA Ajustado = (Investimento × 1,1215) ÷ Compras · "
            "CPA Meta = valor reportado pela plataforma · "
            "CTR = Cliques ÷ Impressões · "
            "CPM = custo por mil impressões · "
            "Ticket Médio = Receita ÷ Compras"
        }, "annotations": {"italic": True, "color": "gray"}}]}
    })
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

    legenda_campanhas = {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content":
            "📌 Legenda: Invest. = gasto bruto Meta Ads · ROAS = Receita ÷ Investimento · "
            "CPA Ajust. = (Investimento × 1,1215) ÷ Compras · CTR = Cliques ÷ Impressões · "
            "CPM = custo por mil impressões (plataforma)"
        }, "annotations": {"italic": True, "color": "gray"}}]}
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
        legenda_campanhas,
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
    """Budget allocation — tabela com Campanha, Investimento, % Budget e ROAS."""
    total = sum(float(c.get("investimento") or 0) for c in campanhas)
    if total == 0:
        return []

    def _cell(text: str, bold: bool = False, color: str = "default") -> dict:
        return {"type": "table_cell", "table_cell": {"rich_text": [{
            "type": "text",
            "text": {"content": text},
            "annotations": {"bold": bold, "color": color},
        }]}}

    header_row = {
        "object": "block", "type": "table_row",
        "table_row": {"cells": [
            [{"type": "text", "text": {"content": "Campanha"}, "annotations": {"bold": True}}],
            [{"type": "text", "text": {"content": "Investimento"}, "annotations": {"bold": True}}],
            [{"type": "text", "text": {"content": "% Budget"}, "annotations": {"bold": True}}],
            [{"type": "text", "text": {"content": "ROAS"}, "annotations": {"bold": True}}],
        ]}
    }

    rows = [header_row]
    for c in sorted(campanhas, key=lambda x: float(x.get("investimento") or 0), reverse=True):
        invest  = float(c.get("investimento") or 0)
        receita = float(c.get("valor_compras") or 0)
        roas    = round(receita / invest, 2) if invest > 0 else None
        pct     = calcular_pct_budget(invest, total)
        nome    = c.get("campanha", "")
        roas_txt = f"{roas:.2f}x" if roas else "—"

        rows.append({
            "object": "block", "type": "table_row",
            "table_row": {"cells": [
                [{"type": "text", "text": {"content": nome}}],
                [{"type": "text", "text": {"content": _fmt_brl(invest)}}],
                [{"type": "text", "text": {"content": _fmt_pct(pct)}}],
                [{"type": "text", "text": {"content": roas_txt}}],
            ]}
        })

    legenda_alocacao = {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content":
            "📌 Legenda: % Budget = Investimento da campanha ÷ Total investido × 100 · "
            "ROAS = Receita ÷ Investimento bruto (sem imposto)"
        }, "annotations": {"italic": True, "color": "gray"}}]}
    }

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "💰 Alocação de Budget"}}]}},
        {
            "object": "block", "type": "table",
            "table": {
                "table_width": 4,
                "has_column_header": True,
                "has_row_header": False,
                "children": rows,
            }
        },
        legenda_alocacao,
        {"object": "block", "type": "divider", "divider": {}},
    ]
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
        ("ROAS",           resumo_atual.get("roas"),          resumo_anterior.get("roas"),          _fmt_x),
        ("Invest. Meta",   resumo_atual.get("total_invest"),   resumo_anterior.get("total_invest"),  _fmt_brl),
        ("Invest. Meta + Imposto", resumo_atual.get("invest_imp"), resumo_anterior.get("invest_imp"), _fmt_brl),
        ("Invest. Total (Meta+Google)",
            (resumo_atual.get("invest_imp") or 0) + (resumo_atual.get("invest_google") or 0) or None,
            (resumo_anterior.get("invest_imp") or 0) + (resumo_anterior.get("invest_google") or 0) or None,
            _fmt_brl),
        ("Receita Meta",   resumo_atual.get("total_receita"),  resumo_anterior.get("total_receita"), _fmt_brl),
        ("Receita Shopify",resumo_atual.get("receita_shopify"),resumo_anterior.get("receita_shopify"),_fmt_brl),
        ("MER",
            round((resumo_atual.get("receita_shopify") or 0) /
                  ((resumo_atual.get("invest_imp") or 0) + (resumo_atual.get("invest_google") or 0))
                  , 2)
            if ((resumo_atual.get("invest_imp") or 0) + (resumo_atual.get("invest_google") or 0)) > 0
               and resumo_atual.get("receita_shopify") else None,
            round((resumo_anterior.get("receita_shopify") or 0) /
                  ((resumo_anterior.get("invest_imp") or 0) + (resumo_anterior.get("invest_google") or 0))
                  , 2)
            if ((resumo_anterior.get("invest_imp") or 0) + (resumo_anterior.get("invest_google") or 0)) > 0
               and resumo_anterior.get("receita_shopify") else None,
            _fmt_x),
        ("Compras",        resumo_atual.get("total_compras"),  resumo_anterior.get("total_compras"), lambda v: str(int(v)) if v is not None else "—"),
        ("CPA",            resumo_atual.get("cpa"),            resumo_anterior.get("cpa"),           _fmt_brl),
        ("CTR",            resumo_atual.get("ctr"),            resumo_anterior.get("ctr"),           _fmt_pct),
        ("CPC",            resumo_atual.get("cpc_medio"),      resumo_anterior.get("cpc_medio"),     _fmt_brl),
        ("CPM",            resumo_atual.get("cpm_medio"),      resumo_anterior.get("cpm_medio"),     _fmt_brl),
    ]

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
        {
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content":
                "📌 Legenda: ↑↓ variação percentual em relação ao período anterior · "
                "ROAS = Receita ÷ Investimento · "
                "CPA Ajust. = (Investimento × 1,1215) ÷ Compras · "
                "Valores negativos de CPA/CAC indicam melhora (custo menor)"
            }, "annotations": {"italic": True, "color": "gray"}}]}
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]


def _blocos_funil(resumo: dict) -> list[dict]:
    """
    Funil de conversão em tabela:
    Etapa | Volume | Taxa (entre etapas)
    Alcance → Impressões → Cliques → Carrinho → Checkout → Compras.
    """
    r = resumo
    impressoes = r.get("total_impressoes") or 0
    alcance    = r.get("total_alcance") or 0
    cliques    = r.get("total_cliques") or 0
    carrinho   = r.get("total_carrinho") or 0
    checkout   = r.get("total_checkout") or 0
    compras    = r.get("total_compras") or 0
    frequencia = r.get("frequencia_media")

    # Taxas entre etapas
    freq_str     = f"{float(frequencia):.2f}x" if frequencia else "—"
    ctr_str      = f"{cliques / impressoes * 100:.2f}%" if impressoes and cliques else "—"
    cliq_carr    = f"{carrinho / cliques * 100:.1f}%" if cliques and carrinho else "—"
    carr_chk     = f"{checkout / carrinho * 100:.1f}%" if carrinho and checkout else "—"
    chk_cmp      = f"{compras / checkout * 100:.1f}%" if checkout and compras else "—"
    efic_geral   = f"{compras / alcance * 100:.3f}%" if alcance and compras else "—"

    header = ["Etapa", "Volume", "Taxa"]
    etapas = [
        ("👤 Alcance",            _fmt_n(alcance),    "—"),
        ("👁️ Impressões",         _fmt_n(impressoes), f"freq. {freq_str}"),
        ("🖱️ Cliques no Link",    _fmt_n(cliques),    f"CTR {ctr_str}"),
        ("🛒 Adições ao Carrinho", _fmt_n(carrinho),   f"{cliq_carr} dos cliques"),
        ("💳 Checkouts Iniciados", _fmt_n(checkout),   f"{carr_chk} do carrinho"),
        ("✅ Compras",             _fmt_n(compras),    f"{chk_cmp} do checkout"),
        ("🎯 Taxa Alcance→Compra", efic_geral,         "—"),
    ]

    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}}
    ]
    for etapa, volume, taxa in etapas:
        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": etapa}}],
            [{"text": {"content": volume}}],
            [{"text": {"content": taxa}}],
        ]}})

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🔽 Funil de Conversão"}}]}},
        {
            "object": "block", "type": "table", "table": {
                "table_width": 3,
                "has_column_header": True,
                "has_row_header": False,
                "children": table_rows,
            }
        },
        {
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content":
                "📌 Legenda: CTR = Cliques ÷ Impressões · Taxa entre etapas = volume da etapa atual ÷ volume da etapa anterior · "
                "Frequência = impressões ÷ alcance único · Taxa Alcance→Compra = eficiência geral da campanha"
            }, "annotations": {"italic": True, "color": "gray"}}]}
        },
        {"object": "block", "type": "divider", "divider": {}},
    ]


# ── Novas funções para relatório mensal ─────────────────────────────────────


def _blocos_metricas_site_semanal(
    data_inicio: date,
    data_fim: date,
    shopify: dict = None,
    invest_meta: float = None,
) -> list[dict]:
    """
    Seção Métricas de Site para o relatório semanal.
    Usa dados reais do Shopify quando disponíveis.

    Colunas: Semana | % Conversão | Ticket Médio | N. Usuários |
             Novos Clientes | CAC | % Desconto | Pedidos > R$300 | Receita Total Shopify
    """
    periodo_str = f"{data_inicio.strftime('%d/%m')} a {data_fim.strftime('%d/%m/%Y')}"

    if shopify:
        receita      = shopify.get("receita_total")
        ticket       = shopify.get("ticket_medio")
        novos        = shopify.get("novos_clientes")
        pct_desc     = shopify.get("pct_desconto")
        ped_300      = shopify.get("pedidos_acima_300")
        usuarios     = shopify.get("usuarios")        # None (requer read_analytics)
        pct_conv     = shopify.get("pct_conversao")   # None (requer read_analytics)
        # CAC = Invest. Meta ajustado / Novos Clientes
        from relatorio.regras import spend_com_imposto
        cac = round(spend_com_imposto(invest_meta) / novos, 2) if (invest_meta and novos) else None

        row = [
            periodo_str,
            f"{pct_conv:.2f}%" if pct_conv is not None else "—",
            _fmt_brl(ticket),
            str(usuarios) if usuarios is not None else "—",
            str(novos) if novos is not None else "—",
            _fmt_brl(cac),
            f"{pct_desc:.1f}%" if pct_desc is not None else "—",
            str(ped_300) if ped_300 is not None else "—",
            _fmt_brl(receita),
        ]
        callout = None
    else:
        row = [periodo_str, "—", "—", "—", "—", "—", "—", "—", "—"]
        callout = {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "⏳ Métricas de site indisponíveis (Shopify offline)."}}],
                "icon": {"type": "emoji", "emoji": "⏳"},
                "color": "yellow_background",
            }
        }

    header = [
        "Semana", "% Conversão", "Ticket Médio", "N. Usuários",
        "Novos Clientes", "CAC", "% Desconto", "Pedidos > R$300", "Receita Total Shopify",
    ]

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📊 Métricas de Site"}}]}},
    ]
    if callout:
        blocos.append(callout)
    blocos.append(_mk_table_notion(header, [row]))
    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


def _blocos_alocacao_canais(resumo: dict) -> list[dict]:
    """
    Seção 3 — Alocação Budget por Canal.
    Meta Ads com valor real + Google Ads como placeholder.
    Inclui coluna % do Total calculada sobre canais com dados reais.
    """
    meta_invest  = float(resumo.get("total_invest") or 0)
    # Total considera apenas canais com dados reais (Google pendente = 0)
    total_canais = meta_invest  # expandir quando Google Ads integrado

    def _pct(valor: float) -> str:
        if total_canais == 0 or valor == 0:
            return "—"
        return f"{valor / total_canais * 100:.1f}%"

    header = ["Canal", "Investimento", "% do Total"]
    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}},
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": "Meta Ads"}}],
            [{"text": {"content": _fmt_brl(meta_invest)}}],
            [{"text": {"content": _pct(meta_invest)}}],
        ]}},
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": "Google Ads"}}],
            [{"text": {"content": "— (integração pendente)"}}],
            [{"text": {"content": "—"}}],
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


# ── Visão Mensal — análise narrativa automática ─────────────────────────────


def _buscar_dados_diarios(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Retorna lista de dicts {data, invest, compras, receita, roas} por dia.
    Reutilizada tanto pela tabela diária quanto pela narrativa mensal.
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

    resultado = []
    for row in rows:
        invest  = float(row["invest"] or 0)
        compras = int(row["compras"] or 0)
        receita = float(row["receita"] or 0)
        roas    = round(receita / invest, 4) if invest > 0 else None
        resultado.append({
            "data":    row["dia"],
            "invest":  invest,
            "compras": compras,
            "receita": receita,
            "roas":    roas,
        })
    return resultado


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



# ── Dados históricos hardcoded (Jun/26) ──────────────────────────────────────

_DADOS_HISTORICO = {
    "Abr/26": {
        "receita_meta": 1343195, "compras_meta": 4474, "ticket_meta": 290,
        "roas_meta": 3.12, "invest_meta": 429901,
        "receita_google": 114260, "compras_google": 257, "ticket_google": 444, "roas_google": 6.35,
        "invest_google": 17979, "frete": 56949, "taxa_desconto": 9.02,
        "novos_clientes": 4211, "cac": 95.29,
    },
    "Mai/26": {
        "receita_meta": 1038126, "compras_meta": 3530, "ticket_meta": 283,
        "roas_meta": 3.06, "invest_meta": 339198,
        "receita_google": 111990, "compras_google": 268, "ticket_google": 417, "roas_google": 4.33,
        "invest_google": 25852, "frete": 45332, "taxa_desconto": 9.28,
        "novos_clientes": 3050, "cac": 119.68,
    },
    "Jun/26": {
        "receita_meta": 850380, "compras_meta": 2629, "ticket_meta": 323,
        "roas_meta": 3.48, "invest_meta": 244259,
        "receita_google": 87640, "compras_google": 211, "ticket_google": 415, "roas_google": 7.10,
        "invest_google": 12329, "frete": 30847, "taxa_desconto": 5.75,
        "novos_clientes": 2007, "cac": 127.74,
    },
}

_META_Q3 = {
    "receita_meta": 1000000, "roas_meta": 4.0, "ticket_meta": 350,
    "taxa_conversao": 6.5, "compras_meta": 2857, "invest_meta": 250000,
}

_RECOMPRAS_HISTORICO = [
    ("2024/03", "14,29%", 14), ("2024/04", "21,62%", 8), ("2024/05", "18,52%", 15),
    ("2024/06", "10,68%", 11), ("2024/07", "8,46%", 11), ("2024/08", "6,52%", 18),
    ("2024/09", "5,91%", 24), ("2024/10", "12,02%", 41), ("2024/11", "18,20%", 93),
    ("2024/12", "16,81%", 57), ("2025/01", "11,25%", 97), ("2025/02", "11,45%", 94),
    ("2025/03", "25,07%", 175), ("2025/04", "21,32%", 94), ("2025/05", "25,00%", 165),
    ("2025/06", "19,06%", 190), ("2025/07", "15,48%", 178), ("2025/08", "17,69%", 328),
    ("2025/09", "23,57%", 404), ("2025/10", "19,59%", 473), ("2025/11", "38,65%", 853),
    ("2025/12", "20,06%", 459), ("2026/01", "21,43%", 555), ("2026/02", "36,17%", 544),
    ("2026/03", "31,78%", 773), ("2026/04", "11,98%", 585), ("2026/05", "22,81%", 882),
    ("2026/06", "32,66%", 941),
]

_RECOMPRAS_ESTADOS = [
    ("São Paulo", 11), ("Minas Gerais", 9), ("Rio de Janeiro", 8),
    ("Paraná", 7), ("Rio Grande do Sul", 6), ("Santa Catarina", 5),
    ("Bahia", 4), ("Goiás", 3), ("Pernambuco", 3), ("Ceará", 2),
    ("Distrito Federal", 2), ("Espírito Santo", 2), ("Mato Grosso", 2),
    ("Maranhão", 1), ("Pará", 1), ("Amazonas", 1), ("Tocantins", 1),
    ("Alagoas", 1), ("Sergipe", 1), ("Rondônia", 1), ("Amapá", 1),
    ("Acre", 1), ("Piauí", 1), ("Paraíba", 1), ("Rio Grande do Norte", 1),
    ("Mato Grosso do Sul", 1),
]

_MESES_PT_ABR = {
    1: "jan.", 2: "fev.", 3: "mar.", 4: "abr.",
    5: "mai.", 6: "jun.", 7: "jul.", 8: "ago.",
    9: "set.", 10: "out.", 11: "nov.", 12: "dez.",
}


def _fmt_data_pt(data_str: str) -> str:
    """Formata data ISO como 'd de mes_pt de ano' (ex: '1 de jun. de 2026')."""
    try:
        d = date.fromisoformat(str(data_str))
        return f"{d.day} de {_MESES_PT_ABR[d.month]} de {d.year}"
    except Exception:
        return str(data_str)


def _mk_table_notion(headers: list, data_rows: list) -> dict:
    """Helper: monta bloco Notion table (object block) com header + linhas de dados."""
    n = len(headers)
    rows = [
        {
            "object": "block", "type": "table_row",
            "table_row": {"cells": [
                [{"type": "text", "text": {"content": h}, "annotations": {"bold": True}}]
                for h in headers
            ]}
        }
    ]
    for dr in data_rows:
        rows.append({
            "object": "block", "type": "table_row",
            "table_row": {"cells": [
                [{"type": "text", "text": {"content": str(v)}}]
                for v in dr
            ]}
        })
    # Garantir ao menos 1 linha de dados além do header (regra Notion)
    if not data_rows:
        rows.append({
            "object": "block", "type": "table_row",
            "table_row": {"cells": [[{"type": "text", "text": {"content": "—"}}] for _ in headers]}
        })
    return {
        "object": "block", "type": "table",
        "table": {
            "table_width": n,
            "has_column_header": True,
            "has_row_header": False,
            "children": rows,
        }
    }


def _blocos_pedidos_sku(
    dados_sku: list[dict] = None,
) -> list[dict]:
    """
    Seção — Pedidos por SKU.

    Tabela completa: SKU | Pedidos | Receita | % Pedidos | % Receita

    dados_sku: lista de dicts com chaves sku, pedidos, receita.
               Se None ou vazio, exibe estrutura com placeholder (integração Shopify pendente).
    """
    SKUS_HISTORICOS = [
        "Kit Completo Source of",
        "Duo - Água Micelar Demaquilante 200 ml",
        "Trio - Hidratação Corporal",
        "Duo - Hidratante Corporal 300 ml",
        "Rotina Completa - 1 Hidratante Corporal + 1 Gel Creme Facial + 1 Água Micelar",
        "Kit Travel Size Source of",
        "Duo - 1 Hidratante Corporal e 1 Gel Creme Facial",
        "Água Micelar Demaquilante TRAVEL SIZE",
        "Sacola de presente",
        "Duo - 2 Gel Creme Facial 50g cada",
        "Água Micelar Demaquilante 200ml",
        "Duo - 1 Água Micelar 200 ml + 1 Hidratante Corporal 300 ml",
        "Duo - Facial 1 Gel Creme Facial + 1 Água Micelar",
        "Hidratante Corporal + Travel Size",
        "Hidratante Corporal Travel Size",
        "Hidratante Corporal 300 ml",
        "Trio - 2 Gel Creme Facial 50g cada + 1 Água Micelar 200ml",
        "Gel Creme Facial 50g",
        "Trio - 3 Águas Micelar Demaquilante 200 ml cada",
    ]

    header = ["SKU", "Pedidos", "Receita", "% Pedidos", "% Receita"]

    tem_dados = bool(dados_sku)

    if tem_dados:
        total_pedidos = sum(int(d.get("pedidos") or 0) for d in dados_sku)
        total_receita = sum(float(d.get("receita") or 0) for d in dados_sku)
        # Ordena por pedidos desc
        dados_sku = sorted(dados_sku, key=lambda d: int(d.get("pedidos") or 0), reverse=True)
        rows = [
            [
                d.get("sku", "—"),
                str(int(d.get("pedidos") or 0)),
                _fmt_brl(d.get("receita")),
                f"{int(d.get('pedidos') or 0) / total_pedidos * 100:.1f}%" if total_pedidos else "—",
                f"{float(d.get('receita') or 0) / total_receita * 100:.1f}%" if total_receita else "—",
            ]
            for d in dados_sku
        ]
        # Linha de total
        rows.append([
            "TOTAL",
            str(total_pedidos),
            _fmt_brl(total_receita),
            "100.0%",
            "100.0%",
        ])
        callout = None
    else:
        # Placeholder: SKUs históricos sem volumes
        rows = [[sku, "—", "—", "—", "—"] for sku in SKUS_HISTORICOS]
        callout = {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "⏳ Volumes de pedidos pendentes de integração Shopify. SKUs listados por ordem de popularidade histórica."}}],
                "icon": {"type": "emoji", "emoji": "⏳"},
                "color": "yellow_background",
            }
        }

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📦 Pedidos por SKU"}}]}},
    ]
    if callout:
        blocos.append(callout)
    blocos.append(_mk_table_notion(header, rows))
    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


def _blocos_pedidos_estado(
    dados_estado: list[dict] = None,
) -> list[dict]:
    """
    Seção — Pedidos por Estado.

    Tabela completa: Estado | Pedidos | Receita | % Pedidos | % Receita

    dados_estado: lista de dicts com chaves estado, pedidos, receita.
                  Se None ou vazio, exibe estrutura com placeholder.
    """
    ESTADOS_HISTORICOS = [
        "São Paulo", "Paraíba", "Distrito Federal", "Goiás", "Sergipe",
        "Tocantins", "Alagoas", "Acre", "Piauí", "Rondônia", "Amapá",
        "Rio Grande do Sul", "Mato Grosso", "Bahia", "Espírito Santo",
        "Pernambuco", "Rio de Janeiro", "Minas Gerais", "Maranhão",
        "Paraná", "Mato Grosso do Sul", "Rio Grande do Norte", "Pará",
        "Amazonas", "Santa Catarina", "Ceará",
    ]

    header = ["Estado", "Pedidos", "Receita", "% Pedidos", "% Receita"]

    tem_dados = bool(dados_estado)

    if tem_dados:
        total_pedidos = sum(int(d.get("pedidos") or 0) for d in dados_estado)
        total_receita = sum(float(d.get("receita") or 0) for d in dados_estado)
        dados_estado  = sorted(dados_estado, key=lambda d: int(d.get("pedidos") or 0), reverse=True)
        rows = [
            [
                d.get("estado", "—"),
                str(int(d.get("pedidos") or 0)),
                _fmt_brl(d.get("receita")),
                f"{int(d.get('pedidos') or 0) / total_pedidos * 100:.1f}%" if total_pedidos else "—",
                f"{float(d.get('receita') or 0) / total_receita * 100:.1f}%" if total_receita else "—",
            ]
            for d in dados_estado
        ]
        rows.append(["TOTAL", str(total_pedidos), _fmt_brl(total_receita), "100.0%", "100.0%"])
        callout = None
    else:
        rows    = [[e, "—", "—", "—", "—"] for e in ESTADOS_HISTORICOS]
        callout = {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "⏳ Volumes de pedidos por estado pendentes de integração Shopify. Estados listados por presença histórica."}}],
                "icon": {"type": "emoji", "emoji": "⏳"},
                "color": "yellow_background",
            }
        }

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🗺️ Pedidos por Estado"}}]}},
    ]
    if callout:
        blocos.append(callout)
    blocos.append(_mk_table_notion(header, rows))
    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


def _blocos_recompradoras(
    dados_recompras_sku: list[dict] = None,
    dados_assiduidade: list[dict] = None,
) -> list[dict]:
    """
    Seção — Base de Recompradoras.

    dados_recompras_sku: [{sku, recompras, pct_recompras}] via shopify.client
    dados_assiduidade:   [{faixa, clientes, pct}] via shopify.client
    """
    # Tabela histórico mensal (hardcoded — vem do CRM)
    headers_hist = ["Mês", "Taxa de Recompra", "Volume de Recompras"]
    rows_hist = [[m, t, str(v)] for m, t, v in _RECOMPRAS_HISTORICO]

    # ── SKU de Entrada com recompras reais (Shopify) ──────────────────────
    headers_sku_entrada = ["SKU de Entrada", "Recompras", "% das Recompras"]
    if dados_recompras_sku:
        total_recompras = sum(d.get("recompras", 0) for d in dados_recompras_sku)
        rows_sku_entrada = [
            [
                d.get("sku", "—"),
                str(d.get("recompras", 0)),
                f"{d.get('pct_recompras', 0):.1f}%",
            ]
            for d in dados_recompras_sku
        ]
        if total_recompras:
            rows_sku_entrada.append(["TOTAL", str(total_recompras), "100.0%"])
        callout_sku = None
    else:
        SKUS_ENTRADA_HISTORICOS = [
            "Duo - Hidratante Corporal 300 ml",
            "Trio - 2 Gel Creme Facial 50g cada + 1 Água Micelar 200ml",
            "Sérum Facial Noturno 30ml",
            "Rotina Completa - 1 Hidratante Corporal + 1 Gel Creme Facial + 1 Água Micelar",
            "Água Micelar Demaquilante 200ml",
            "Água Micelar Demaquilante TRAVEL SIZE",
            "Kit Travel Size Source of",
            "Gel Creme Facial 50g",
            "Hidratante Corporal Travel Size",
            "Hidratante Corporal + Travel Size",
            "Duo - 2 Gel Creme Facial 50g cada",
            "Kit Completo Source of",
            "Hidratante Corporal 300 ml",
            "Trio - Hidratação Corporal",
            "Duo - Facial 1 Gel Creme Facial + 1 Água Micelar",
            "Duo - 1 Água Micelar 200 ml + 1 Hidratante Corporal 300 ml",
            "Sacola de presente",
            "Duo - 1 Hidratante Corporal e 1 Gel Creme Facial",
            "LEVE 4 PAGUE 3 - Hidratação Corporal",
            "Embalagem de presente",
        ]
        rows_sku_entrada = [[sku, "—", "—"] for sku in SKUS_ENTRADA_HISTORICOS]
        callout_sku = {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "⏳ Volume de recompras por SKU pendente de integração Shopify."}}],
                "icon": {"type": "emoji", "emoji": "⏳"},
                "color": "yellow_background",
            }
        }

    # ── Assiduidade de clientes (Shopify) ─────────────────────────────────
    headers_assid = ["Faixa de Pedidos", "Clientes", "% dos Clientes"]
    if dados_assiduidade:
        rows_assid = [
            [d.get("faixa", "—"), str(d.get("clientes", 0)), f"{d.get('pct', 0):.1f}%"]
            for d in dados_assiduidade
        ]
        callout_assid = None
    else:
        rows_assid = [
            ["1 pedido",    "—", "—"],
            ["2 pedidos",   "—", "—"],
            ["3 pedidos",   "—", "—"],
            ["4-5 pedidos", "—", "—"],
            ["6+ pedidos",  "—", "—"],
        ]
        callout_assid = {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "⏳ Dados de assiduidade pendentes de integração Shopify."}}],
                "icon": {"type": "emoji", "emoji": "⏳"},
                "color": "yellow_background",
            }
        }

    # ── Recompras por Estado (hardcoded) ──────────────────────────────────
    headers_estados = ["Estado", "Número de Recompras"]
    rows_estados = [[e, str(n)] for e, n in _RECOMPRAS_ESTADOS]

    # ── Monta blocos ──────────────────────────────────────────────────────
    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "👥 Base de Recompradoras"}}]}},
        {"object": "block", "type": "divider", "divider": {}},

        # Histórico mensal
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Histórico de Recompra"}}]}},
        _mk_table_notion(headers_hist, rows_hist),
        {"object": "block", "type": "divider", "divider": {}},

        # SKU de Entrada
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "SKU de Entrada"}}]}},
    ]
    if callout_sku:
        blocos.append(callout_sku)
    blocos.append(_mk_table_notion(headers_sku_entrada, rows_sku_entrada))
    blocos.append({"object": "block", "type": "divider", "divider": {}})

    # Assiduidade
    blocos.append({
        "object": "block", "type": "heading_3",
        "heading_3": {"rich_text": [{"text": {"content": "Assiduidade de Clientes"}}]}
    })
    if callout_assid:
        blocos.append(callout_assid)
    blocos.append(_mk_table_notion(headers_assid, rows_assid))
    blocos.append({"object": "block", "type": "divider", "divider": {}})

    # Recompras por Estado
    blocos.append({
        "object": "block", "type": "heading_3",
        "heading_3": {"rich_text": [{"text": {"content": "Recompras por Estado"}}]}
    })
    blocos.append(_mk_table_notion(headers_estados, rows_estados))
    blocos.append({"object": "block", "type": "divider", "divider": {}})

    return blocos


_MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março",    4: "Abril",
    5: "Maio",    6: "Junho",     7: "Julho",    8: "Agosto",
    9: "Setembro",10: "Outubro",  11: "Novembro", 12: "Dezembro",
}

