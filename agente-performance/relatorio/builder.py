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


def _blocos_analise_ultima_semana(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Seção 1 — Análise da Última Semana.
    Busca dados das 2 últimas semanas do mês, gera texto narrativo
    e 3 tabelas separadas: Métricas de Site/CRM, Meta Ads, Google Ads.
    """
    # Semana 2 (última): últimos 7 dias do mês
    sem2_fim = data_fim
    sem2_ini = data_fim - timedelta(days=6)
    # Semana 1 (penúltima): 7 dias anteriores à última semana
    # data_fim - 13 dias até data_fim - 7 dias
    sem1_ini = data_fim - timedelta(days=13)
    sem1_fim = data_fim - timedelta(days=7)

    def _label(ini: date, fim: date) -> str:
        return f"{ini.strftime('%d/%m')} a {fim.strftime('%d/%m')}"

    def _buscar_semana(ini: date, fim: date) -> dict:
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
    label1 = _label(sem1_ini, sem1_fim)
    label2 = _label(sem2_ini, sem2_fim)

    # ── Texto narrativo ───────────────────────────────────────────────────
    def _comp(atual, anterior, fmt_fn):
        if anterior and anterior != 0:
            var = (atual - anterior) / abs(anterior) * 100
            sinal = "acima" if var > 0 else "abaixo"
            return f"{fmt_fn(atual)} ({abs(var):.1f}% {sinal} da semana anterior)"
        return fmt_fn(atual)

    narrativa_partes = [
        f"Na última semana do mês ({label2}), "
        f"o investimento em Meta Ads foi de {_comp(s2['invest'], s1['invest'], _fmt_brl)}, "
        f"com ROAS de {_fmt_x(s2['roas'])}"
    ]
    if s1["roas"] and s2["roas"]:
        var_roas = (s2["roas"] - s1["roas"]) / abs(s1["roas"]) * 100
        tend = "acima" if var_roas > 0 else "abaixo"
        narrativa_partes[-1] += f" ({abs(var_roas):.1f}% {tend} da semana anterior)"
    narrativa_partes[-1] += "."
    narrativa_partes.append(
        f"Foram registradas {s2['compras']} transações, "
        f"com receita de {_comp(s2['receita'], s1['receita'], _fmt_brl)}. "
        f"Ticket médio: {_fmt_brl(s2['ticket'])}."
    )
    narrativa = " ".join(narrativa_partes)

    # ── Helper: montar bloco table ────────────────────────────────────────
    def _mk_table(headers: list[str], data_rows: list[list[str]]) -> dict:
        """Monta bloco Notion table com header + linhas de dados."""
        n = len(headers)
        rows = [
            {
                "object": "block", "type": "table_row",
                "table_row": {"cells": [
                    [{"type": "text", "text": {"content": h},
                      "annotations": {"bold": True}}]
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
        return {
            "object": "block", "type": "table",
            "table": {
                "table_width": n,
                "has_column_header": True,
                "has_row_header": False,
                "children": rows,
            }
        }

    # ── Tabela 1 — Métricas de Site/CRM (9 colunas) — dados reais Shopify ──
    headers_site = [
        "Semana", "% Conversão", "Ticket Médio",
        "N. Usuários", "Novos Clientes", "CAC", "% Desconto", "Pedidos > R$300",
        "Receita Total Shopify",
    ]

    def _row_site_shopify(label: str, ini: date, fim: date, invest_meta: float) -> list:
        """Busca métricas de site reais do Shopify para o período."""
        try:
            from shopify.client import metricas_site as _ms
            from relatorio.regras import spend_com_imposto
            ms   = _ms(ini, fim)
            novos = ms.get("novos_clientes")
            cac   = round(spend_com_imposto(invest_meta) / novos, 2) if (invest_meta and novos) else None
            return [
                label,
                f"{ms['pct_conversao']:.2f}%" if ms.get("pct_conversao") is not None else "—",
                _fmt_brl(ms.get("ticket_medio")),
                str(ms["usuarios"]) if ms.get("usuarios") is not None else "—",
                str(novos) if novos is not None else "—",
                _fmt_brl(cac),
                f"{ms['pct_desconto']:.1f}%" if ms.get("pct_desconto") is not None else "—",
                str(ms.get("pedidos_acima_300", "—")),
                _fmt_brl(ms.get("receita_total")),
            ]
        except Exception as _e:
            logger.warning("[Builder] Shopify metricas_site falhou (%s → %s): %s", ini, fim, _e)
            return [label, "—", "—", "—", "—", "—", "—", "—", "—"]

    rows_site = [
        _row_site_shopify(label1, sem1_ini, sem1_fim, s1.get("invest")),
        _row_site_shopify(label2, sem2_ini, sem2_fim, s2.get("invest")),
    ]

    # ── Tabela 2 — Meta Ads por semana (7 colunas) ────────────────────────
    headers_meta = [
        "Semana", "Investimento Meta", "Investimento Meta + Imposto",
        "Transações", "Receita", "ROAS Meta", "Ticket Médio"
    ]
    rows_meta = [
        [
            label1,
            _fmt_brl(s1["invest"]),
            _fmt_brl(s1["invest_imp"]),
            str(s1["compras"]) if s1["compras"] else "—",
            _fmt_brl(s1["receita"]),
            _fmt_x(s1["roas"]),
            _fmt_brl(s1["ticket"]),
        ],
        [
            label2,
            _fmt_brl(s2["invest"]),
            _fmt_brl(s2["invest_imp"]),
            str(s2["compras"]) if s2["compras"] else "—",
            _fmt_brl(s2["receita"]),
            _fmt_x(s2["roas"]),
            _fmt_brl(s2["ticket"]),
        ],
    ]

    # ── Tabela 3 — Google Ads por semana (6 colunas, placeholder) ─────────
    headers_google = [
        "Semana", "Investimento Google",
        "Transações", "Receita", "ROAS Google", "Ticket Médio"
    ]
    rows_google = [
        [label1, "—", "—", "—", "—", "—"],
        [label2, "—", "—", "—", "—", "—"],
    ]

    return [
        # Heading principal
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📝 Análise da Última Semana"}}]}},

        # Callout narrativo
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": narrativa}}],
                "icon": {"type": "emoji", "emoji": "📝"},
                "color": "blue_background",
            }
        },

        {"object": "block", "type": "divider", "divider": {}},

        # --- Tabela 1: Métricas de Site ---
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Métricas de Site"}}]}},
        _mk_table(headers_site, rows_site),

        {"object": "block", "type": "divider", "divider": {}},

        # --- Tabela 2: Meta Ads ---
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Meta Ads"}}]}},
        _mk_table(headers_meta, rows_meta),

        {"object": "block", "type": "divider", "divider": {}},

        # --- Tabela 3: Google Ads (placeholder) ---
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Google Ads"}}]}},
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "⏳ Dados Google Ads pendentes de integração."}}],
                "icon": {"type": "emoji", "emoji": "⏳"},
                "color": "yellow_background",
            }
        },
        _mk_table(headers_google, rows_google),

        {"object": "block", "type": "divider", "divider": {}},
    ]


def _blocos_visao_mensal_diaria(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
    dados_diarios: list = None,
) -> list[dict]:
    """
    Seção Visão Mensal — 3 tabelas separadas:
    1. Consolidado: Data | Invest. Meta+Imposto | Receita Shopify | MER
    2. Meta Ads:    Data | Investimento | Compras | CPA | Receita Meta | ROAS
    3. Google Ads:  Data | Investimento | Compras | CPA | Receita | ROAS (placeholder)
    """
    if dados_diarios is None:
        dados_diarios = _buscar_dados_diarios(data_inicio, data_fim, db_path)

    # Busca receita Shopify por dia (com fallback para None)
    shopify_por_dia: dict[str, float] = {}
    try:
        from shopify.client import _paginar_pedidos
        from collections import defaultdict
        _rec_dia: dict[str, float] = defaultdict(float)
        for pedido in _paginar_pedidos(data_inicio, data_fim, campos="id,created_at,total_price"):
            dia_iso = pedido.get("created_at", "")[:10]
            _rec_dia[dia_iso] += float(pedido.get("total_price") or 0)
        shopify_por_dia = dict(_rec_dia)
    except Exception as _e:
        logger.warning("[Builder] Shopify diário indisponível: %s", _e)

    # ── Helper: monta tabela Notion ──────────────────────────────────────
    def _mk_table(header, rows_data):
        table_rows = [
            {"type": "table_row", "table_row": {"cells": [
                [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
            ]}}
        ]
        for r in rows_data:
            table_rows.append({"type": "table_row", "table_row": {"cells": [
                [{"text": {"content": str(v)}}] for v in r
            ]}})
        return {
            "object": "block", "type": "table", "table": {
                "table_width": len(header),
                "has_column_header": True,
                "has_row_header": False,
                "children": table_rows,
            }
        }

    # ── Tabela 1 — Consolidado ───────────────────────────────────────────
    header_cons = ["Data", "Invest. Meta+Imposto", "Receita Shopify", "MER"]
    rows_cons   = []
    tot_invest_imp = 0.0
    tot_rec_shopify = 0.0

    for row in dados_diarios:
        dia_raw  = row.get("data") or row.get("dia", "")
        try:
            dia_date = date.fromisoformat(str(dia_raw)) if not isinstance(dia_raw, date) else dia_raw
            dia_str  = dia_date.strftime("%d/%m")
            dia_iso  = dia_date.isoformat()
        except Exception:
            dia_str = str(dia_raw); dia_iso = str(dia_raw)

        invest_imp   = spend_com_imposto(float(row.get("invest", 0) or 0))
        rec_shopify  = shopify_por_dia.get(dia_iso)
        mer          = round(rec_shopify / invest_imp, 2) if (rec_shopify and invest_imp > 0) else None

        tot_invest_imp  += invest_imp
        if rec_shopify:
            tot_rec_shopify += rec_shopify

        rows_cons.append([
            dia_str,
            _fmt_brl(invest_imp),
            _fmt_brl(rec_shopify) if rec_shopify is not None else "—",
            _fmt_x(mer) if mer is not None else "—",
        ])

    # Linha total consolidado
    mer_total = round(tot_rec_shopify / tot_invest_imp, 2) if (tot_rec_shopify and tot_invest_imp > 0) else None
    rows_cons.append([
        "TOTAL",
        _fmt_brl(tot_invest_imp),
        _fmt_brl(tot_rec_shopify) if tot_rec_shopify else "—",
        _fmt_x(mer_total) if mer_total else "—",
    ])

    # ── Tabela 2 — Meta Ads ──────────────────────────────────────────────
    header_meta = ["Data", "Investimento", "Compras", "CPA", "Receita Meta", "ROAS"]
    rows_meta   = []
    tot_m_invest = tot_m_compras = tot_m_receita = 0.0

    for row in dados_diarios:
        dia_raw = row.get("data") or row.get("dia", "")
        try:
            dia_str = date.fromisoformat(str(dia_raw)).strftime("%d/%m") if not isinstance(dia_raw, date) else dia_raw.strftime("%d/%m")
        except Exception:
            dia_str = str(dia_raw)

        invest  = float(row.get("invest", 0) or 0)
        compras = int(row.get("compras", 0) or 0)
        receita = float(row.get("receita", 0) or 0)
        cpa     = calcular_cpa(invest, compras)
        roas    = round(receita / invest, 2) if invest > 0 else None

        tot_m_invest  += invest
        tot_m_compras += compras
        tot_m_receita += receita

        rows_meta.append([
            dia_str,
            _fmt_brl(invest),
            str(compras) if compras else "—",
            _fmt_brl(cpa),
            _fmt_brl(receita),
            _fmt_x(roas),
        ])

    cpa_total_m  = calcular_cpa(tot_m_invest, int(tot_m_compras))
    roas_total_m = round(tot_m_receita / tot_m_invest, 2) if tot_m_invest > 0 else None
    rows_meta.append([
        "TOTAL",
        _fmt_brl(tot_m_invest),
        str(int(tot_m_compras)) if tot_m_compras else "—",
        _fmt_brl(cpa_total_m),
        _fmt_brl(tot_m_receita),
        _fmt_x(roas_total_m),
    ])

    # ── Tabela 3 — Google Ads (placeholder) ─────────────────────────────
    header_google = ["Data", "Investimento", "Compras", "CPA", "Receita", "ROAS"]
    rows_google   = [[row.get("data") or row.get("dia",""), "—", "—", "—", "—", "—"] for row in dados_diarios]
    rows_google.append(["TOTAL", "—", "—", "—", "—", "—"])
    # Formata datas
    for r in rows_google:
        try:
            r[0] = date.fromisoformat(str(r[0])).strftime("%d/%m")
        except Exception:
            pass

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📊 Visão Mensal"}}]}},

        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Consolidado (Mídia Total)"}}]}},
        _mk_table(header_cons, rows_cons),

        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Meta Ads"}}]}},
        _mk_table(header_meta, rows_meta),

        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Google Ads"}}]}},
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "⏳ Dados Google Ads pendentes de integração."}}],
                "icon": {"type": "emoji", "emoji": "⏳"},
                "color": "yellow_background",
            }
        },
        _mk_table(header_google, rows_google),

        {"object": "block", "type": "divider", "divider": {}},
    ]


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


def _detectar_fases(dados_diarios: list) -> dict:
    """
    Analisa dados diários e detecta padrões em duas fases.

    Retorna dict com fase1, fase2, pico_roas, vale_roas, faixa_eficiente,
    invest_medio_geral, mediana_invest, roas_baixo_invest, roas_alto_invest,
    threshold, n_dias_baixo, n_dias_alto.
    """
    if not dados_diarios:
        return {}

    n = len(dados_diarios)
    metade = n // 2
    fase1_dados = dados_diarios[:metade] if metade > 0 else dados_diarios
    fase2_dados = dados_diarios[metade:] if metade < n else dados_diarios

    def _media(lst, campo):
        vals = [d[campo] for d in lst if d.get(campo) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    dias_com_roas = [d for d in dados_diarios if d["roas"] is not None]
    pico = max(dias_com_roas, key=lambda d: d["roas"]) if dias_com_roas else None
    vale = min(dias_com_roas, key=lambda d: d["roas"]) if dias_com_roas else None

    invests = sorted(d["invest"] for d in dados_diarios)
    if len(invests) % 2 == 0:
        mediana = (invests[len(invests)//2 - 1] + invests[len(invests)//2]) / 2
    else:
        mediana = invests[len(invests)//2]

    dias_baixo = [d for d in dados_diarios if d["invest"] < mediana and d["roas"] is not None]
    dias_alto  = [d for d in dados_diarios if d["invest"] >= mediana and d["roas"] is not None]
    roas_baixo = round(sum(d["roas"] for d in dias_baixo) / len(dias_baixo), 2) if dias_baixo else None
    roas_alto  = round(sum(d["roas"] for d in dias_alto)  / len(dias_alto),  2) if dias_alto  else None

    eficientes = [d for d in dados_diarios if d["roas"] is not None and d["roas"] > 4.0]
    faixa_ef = None
    if eficientes:
        invests_ef = [d["invest"] for d in eficientes]
        faixa_ef = {"invest_min": min(invests_ef), "invest_max": max(invests_ef)}

    invest_medio_geral = round(sum(d["invest"] for d in dados_diarios) / n, 0)

    return {
        "fase1": {
            "invest_medio": _media(fase1_dados, "invest"),
            "roas_medio":   _media(fase1_dados, "roas"),
            "n_dias":       len(fase1_dados),
        },
        "fase2": {
            "invest_medio": _media(fase2_dados, "invest"),
            "roas_medio":   _media(fase2_dados, "roas"),
            "n_dias":       len(fase2_dados),
        },
        "pico_roas":          {"data": pico["data"], "valor": pico["roas"]} if pico else None,
        "vale_roas":          {"data": vale["data"], "valor": vale["roas"]} if vale else None,
        "faixa_eficiente":    faixa_ef,
        "invest_medio_geral": invest_medio_geral,
        "mediana_invest":     round(mediana, 0),
        "roas_baixo_invest":  roas_baixo,
        "roas_alto_invest":   roas_alto,
        "threshold":          round(mediana, 0),
        "n_dias_baixo":       len(dias_baixo),
        "n_dias_alto":        len(dias_alto),
    }


def _gerar_texto_narrativo_mensal(
    resumo: dict,
    dados_diarios: list,
    nome_mes: str,
    ano: int,
) -> tuple:
    """
    Gera textos narrativos para a seção RESUMO do relatório mensal.
    Retorna tupla (abertura, comportamento, sensibilidade).
    """
    r = resumo
    fases = _detectar_fases(dados_diarios)

    invest     = r.get("total_invest", 0)
    invest_imp = r.get("invest_imp", 0)
    receita    = r.get("total_receita", 0)
    compras    = r.get("total_compras", 0)
    roas       = r.get("roas")
    n_dias     = len(dados_diarios)

    roas_str = f"{roas:.2f}x" if roas else "—"
    if roas:
        if roas >= 4.5:
            roas_qualidade = "excepcional"
        elif roas >= 3.5:
            roas_qualidade = "sólida"
        elif roas >= 2.5:
            roas_qualidade = "razoável, mas abaixo do potencial"
        else:
            roas_qualidade = "abaixo do esperado"
        roas_avaliacao = f"com performance {roas_qualidade}"
    else:
        roas_avaliacao = ""

    abertura = (
        f"{nome_mes}/{ano} encerrou com ROAS de {roas_str} {roas_avaliacao}. "
        f"O mês acumulou {_fmt_brl(receita)} em receita Meta Ads sobre "
        f"{_fmt_brl(invest)} de investimento ({_fmt_brl(invest_imp)} com imposto), "
        f"em {n_dias} dias com dados disponíveis e {compras} transações registradas."
    )

    comportamento_partes = []
    if fases and fases.get("fase1") and fases.get("fase2"):
        f1 = fases["fase1"]
        f2 = fases["fase2"]
        if f1["invest_medio"] and f2["invest_medio"]:
            var_invest = (f2["invest_medio"] - f1["invest_medio"]) / f1["invest_medio"] * 100
            if abs(var_invest) >= 15:
                dir_invest = "escalou" if var_invest > 0 else "recuou"
                comportamento_partes.append(
                    f"O mês apresentou mudança de patamar de investimento: "
                    f"na primeira metade ({f1['n_dias']} dias) o gasto médio foi de "
                    f"{_fmt_brl(f1['invest_medio'])}/dia, enquanto na segunda metade "
                    f"({f2['n_dias']} dias) {dir_invest} para "
                    f"{_fmt_brl(f2['invest_medio'])}/dia ({abs(var_invest):.0f}% de variação)."
                )
            else:
                comportamento_partes.append(
                    f"O investimento se manteve relativamente estável ao longo do mês: "
                    f"{_fmt_brl(f1['invest_medio'])}/dia na primeira metade e "
                    f"{_fmt_brl(f2['invest_medio'])}/dia na segunda metade."
                )
        if f1["roas_medio"] and f2["roas_medio"]:
            var_roas = (f2["roas_medio"] - f1["roas_medio"]) / f1["roas_medio"] * 100
            dir_roas = "melhorou" if var_roas > 0 else "caiu"
            comportamento_partes.append(
                f"O ROAS médio {dir_roas} entre as fases: "
                f"{f1['roas_medio']:.2f}x \u2192 {f2['roas_medio']:.2f}x."
            )

    if fases.get("pico_roas") and fases.get("vale_roas"):
        pico = fases["pico_roas"]
        vale = fases["vale_roas"]
        try:
            pico_d = date.fromisoformat(str(pico["data"])).strftime("%d/%m")
            vale_d = date.fromisoformat(str(vale["data"])).strftime("%d/%m")
        except Exception:
            pico_d = str(pico["data"])
            vale_d = str(vale["data"])
        comportamento_partes.append(
            f"Melhor dia: {pico_d} com ROAS {pico['valor']:.2f}x. "
            f"Pior dia: {vale_d} com ROAS {vale['valor']:.2f}x."
        )

    comportamento = (
        " ".join(comportamento_partes)
        if comportamento_partes
        else "Análise de comportamento diário indisponível."
    )

    sensibilidade_parts = []
    if fases.get("roas_baixo_invest") is not None and fases.get("roas_alto_invest") is not None:
        threshold = fases["threshold"]
        rb = fases["roas_baixo_invest"]
        ra = fases["roas_alto_invest"]
        nb = fases.get("n_dias_baixo", 0)
        na = fases.get("n_dias_alto", 0)
        if rb and ra:
            if ra > rb:
                sensibilidade_parts.append(
                    f"Dias com investimento abaixo de {_fmt_brl(threshold)}/dia "
                    f"({nb} dias): ROAS médio {rb:.2f}x. "
                    f"Dias com investimento acima de {_fmt_brl(threshold)}/dia "
                    f"({na} dias): ROAS médio {ra:.2f}x. "
                    f"O canal responde positivamente ao aumento de budget."
                )
            else:
                sensibilidade_parts.append(
                    f"Dias com investimento abaixo de {_fmt_brl(threshold)}/dia "
                    f"({nb} dias): ROAS médio {rb:.2f}x. "
                    f"Dias com investimento acima de {_fmt_brl(threshold)}/dia "
                    f"({na} dias): ROAS médio {ra:.2f}x. "
                    f"A eficiência tende a ser maior em dias de menor escala "
                    f"\u2014 atenção ao ponto de saturação."
                )
        if fases.get("faixa_eficiente"):
            ef = fases["faixa_eficiente"]
            sensibilidade_parts.append(
                f"Faixa de maior eficiência (ROAS > 4.0x): "
                f"{_fmt_brl(ef['invest_min'])} a {_fmt_brl(ef['invest_max'])}/dia."
            )

    sensibilidade = (
        " ".join(sensibilidade_parts)
        if sensibilidade_parts
        else "Análise de sensibilidade indisponível com os dados atuais."
    )
    return abertura, comportamento, sensibilidade


def _blocos_visao_mensal_narrativa(
    resumo: dict,
    dados_diarios: list,
    nome_mes: str,
    ano: int,
) -> list[dict]:
    """
    Seção narrativa estratégica do relatório mensal (Visão Mensal — RESUMO).
    Inserida após Análise da Última Semana e antes da tabela dia-a-dia.
    """
    abertura, comportamento, sensibilidade = _gerar_texto_narrativo_mensal(
        resumo, dados_diarios, nome_mes, ano
    )

    ticket = resumo.get("ticket_medio")
    ticket_str = _fmt_brl(ticket) if ticket else "—"
    if ticket:
        if ticket >= 500:
            ticket_analise = "produto de ticket elevado — foco em qualificação do tráfego é essencial."
        elif ticket >= 200:
            ticket_analise = "ticket médio saudável para e-commerce de moda/lifestyle."
        else:
            ticket_analise = "ticket médio baixo — estratégias de upsell e bundle podem elevar a rentabilidade."
    else:
        ticket_analise = "análise indisponível."

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📊 Visão Mensal"}}]}},
        {"object": "block", "type": "divider", "divider": {}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "RESUMO"}}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"text": {"content": abertura}}]}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "O que sustentou a eficiência"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"Ticket médio: {ticket_str} — {ticket_analise}"
         }}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            "Taxa de descontos: — (fonte: Shopify)"
         }}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            "Frete cobrado: — (fonte: Shopify)"
         }}]}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Comportamento ao longo do mês"}}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"text": {"content": comportamento}}]}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Criativos"}}]}},
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "⏳ Análise qualitativa de criativos a ser preenchida pela equipe Guará."}}],
                "icon": {"type": "emoji", "emoji": "⏳"},
                "color": "yellow_background",
            }
        },
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Pontos de atenção"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            "CAC: — (fonte: Shopify/CRM)"
         }}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content":
            f"Sensibilidade ao investimento: {sensibilidade}"
         }}]}},
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


def _blocos_rentabilidade_aquisicao(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Seção 2c — Rentabilidade de Aquisição Mensal.
    3 sub-seções: Geral, Meta Ads, Google Ads.
    """
    # Query diária do SQLite
    conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            data_inicio,
            SUM(investimento)*1.1215                                              AS gasto_imp,
            SUM(COALESCE(valor_compras, 0))                                       AS receita,
            SUM(investimento)*1.1215 / NULLIF(SUM(COALESCE(compras,0)),0)         AS cpa,
            SUM(COALESCE(compras, 0))                                              AS compras,
            SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento)*1.1215,0)  AS roas_imp
        FROM campanhas
        WHERE data_inicio >= ? AND data_fim <= ?
        GROUP BY data_inicio
        ORDER BY data_inicio
    """, (data_inicio.isoformat(), data_fim.isoformat()))
    rows_db = [dict(r) for r in cur.fetchall()]
    conn.close()

    nome_mes  = _MESES_PT[data_inicio.month]
    ano       = data_inicio.year
    secao_tit = f"💰 Rentabilidade de Aquisição Mensal — {nome_mes}/{ano}"

    # ── Tabela 1: Rentabilidade Geral ─────────────────────────────────────
    headers_geral = ["Data", "Gasto", "Custo por Compra", "CPA Meta (plataforma)"]
    rows_geral = []
    for r in rows_db:
        gasto = float(r["gasto_imp"] or 0)
        cpa   = r["cpa"]
        rows_geral.append([
            _fmt_data_pt(r["data_inicio"]),
            _fmt_brl(gasto) if gasto else "—",
            _fmt_brl(float(cpa)) if cpa else "—",
            "—",  # CPA Meta plataforma (não disponível por dia nessa query)
        ])

    # ── Tabela 2: Meta Ads ────────────────────────────────────────────────
    headers_meta = ["Data", "Gasto c/ Imposto", "Receita Meta", "ROAS Meta"]
    rows_meta = []
    for r in rows_db:
        gasto   = float(r["gasto_imp"] or 0)
        receita = float(r["receita"] or 0)
        roas    = r["roas_imp"]
        rows_meta.append([
            _fmt_data_pt(r["data_inicio"]),
            _fmt_brl(gasto) if gasto else "—",
            _fmt_brl(receita) if receita else "—",
            _fmt_x(float(roas)) if roas else "—",
        ])

    # ── Tabela 3: Google Ads (placeholder, 30 linhas) ─────────────────────
    headers_google = ["Data", "Gasto c/ Imposto", "Receita Google", "ROAS Google"]
    rows_google = []
    # Gera datas do período para o placeholder Google
    dia_atual = data_inicio
    while dia_atual <= data_fim:
        rows_google.append([_fmt_data_pt(dia_atual.isoformat()), "—", "—", "—"])
        dia_atual += timedelta(days=1)

    # Garantir ao menos 1 linha
    if not rows_geral:
        rows_geral = [["—", "—", "—", "—"]]
    if not rows_meta:
        rows_meta = [["—", "—", "—", "—"]]
    if not rows_google:
        rows_google = [["—", "—", "—", "—"]]

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": secao_tit}}]}},
        {"object": "block", "type": "divider", "divider": {}},
        # --- Geral ---
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Geral"}}]}},
        _mk_table_notion(headers_geral, rows_geral),
        {"object": "block", "type": "divider", "divider": {}},
        # --- Meta Ads ---
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Meta Ads"}}]}},
        _mk_table_notion(headers_meta, rows_meta),
        {"object": "block", "type": "divider", "divider": {}},
        # --- Google Ads ---
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Google Ads"}}]}},
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "⏳ Dados Google Ads pendentes de integração."}}],
                "icon": {"type": "emoji", "emoji": "⏳"},
                "color": "yellow_background",
            }
        },
        _mk_table_notion(headers_google, rows_google),
        {"object": "block", "type": "divider", "divider": {}},
    ]


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


def _blocos_planejamento_q3(resumo: dict) -> list[dict]:
    """
    Seção — Planejamento Q3 2026 — Meta Ads (análise estratégica hardcoded).
    """
    def _p(txt):
        return {
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": txt}}]}
        }

    # Tabela resumo cenário-meta (5 colunas)
    headers_cenario = ["Métrica", "Jun/26 (atual)", "Meta Q3", "Variação"]
    rows_cenario = [
        ["Receita mensal", "R$849k", "R$1.000k", "+18%"],
        ["ROAS", "3,63x", "4,00x", "+10%"],
        ["Investimento c/ imposto", "R$234k", "R$250k", "+7%"],
        ["Ticket médio", "R$323", "R$350", "+8%"],
        ["Taxa de conversão", "5,79%", "6,50%", "+12%"],
        ["CPC (máximo tolerável)", "R$5,16", "R$5,69", "—"],
        ["Compras/mês", "2.624", "~2.857", "+9%"],
    ]

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🎯 Planejamento Q3 2026 — Meta Ads"}}]}},
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "Meta: R$1M de receita mensal com ROAS 4,0"}}],
                "icon": {"type": "emoji", "emoji": "🎯"},
                "color": "blue_background",
            }
        },
        {"object": "block", "type": "divider", "divider": {}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Onde Estamos"}}]}},
        _p("Junho fechou com R$849k de receita e ROAS 3.63, o melhor mês em eficiência desde março. O ticket médio subiu de R$288 (abril) para R$323 (junho), um ganho de 12% que sustentou o ROAS mesmo com CPC mais caro. O investimento mensal foi de R$234k (com imposto). Para atingir R$1M com ROAS 4, o investimento necessário é de R$250k/mês, próximo do que já foi operado em junho. O desafio não é de volume, é de eficiência: precisamos ganhar 0.37 pontos de ROAS."),
        {"object": "block", "type": "divider", "divider": {}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "O que os dados mostram"}}]}},
        _p("A fórmula do ROAS da conta é: (Taxa de Conversão × Ticket Médio) / CPC. Junho: (5.79% × R$323) / R$5.16 = 3.63. Para ROAS 4.0, precisamos que esse cálculo chegue em 4."),
        _p("Analisando os 90 dias de histórico, identificamos 13 dias que já entregaram ROAS ≥ 4.0. O perfil médio desses dias:"),
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "Taxa de conversão: 7.13% (vs 5.79% da média de junho)"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "Ticket médio: R$325 (igual à média — não foi o ticket que puxou)"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "CPC: R$5.06 (levemente abaixo da média)"}}]}},
        _p("A taxa de conversão é a variável que mais impacta o ROAS. Nos melhores dias, a conversão estava 23% acima da média. Isso significa que a principal alavanca está no site e na oferta, não no tráfego."),
        {"object": "block", "type": "divider", "divider": {}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "3 Alavancas"}}]}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "1. Ticket médio → de R$323 para R$350"}}]}},
        _p("O ticket já subiu 12% entre abril e junho. Para chegar em R$350, a estratégia é priorizar kits e bundles como oferta principal nos anúncios e na loja, ajustar frete grátis condicionado a valor mínimo e reforçar upsell no checkout."),
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": "Estratégia: Kit Inverno — '3 hidratantes para o inverno todo'. O inverno é o momento certo para essa oferta. Ângulos de teste: 'Sua pele protegida até setembro', 'compre 2 e o 3º com 50% de desconto', 'O kit que substitui sua rotina de inverno'."}}],
                "icon": {"type": "emoji", "emoji": "💡"},
                "color": "green_background",
            }
        },
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "2. Taxa de conversão → de 5.79% para 6.5%"}}]}},
        _p("Essa é a alavanca mais importante. As duas melhores semanas da conta (W23 e W24, com ROAS 3.95 e 4.13) tiveram CVR de 6.06% e 6.33%, próximas da meta. Foco em otimização de landing page, velocidade, provas sociais e redução de atrito no checkout."),
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "3. CPC → manter abaixo de R$5.70"}}]}},
        _p("O CPC vem subindo (R$4.47 em abril → R$5.16 em junho). Manter ritmo de criativos novos para manter CTR saudável e evitar fadiga de audiência."),
        {"object": "block", "type": "divider", "divider": {}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Distribuição Inteligente do Investimento"}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "Por dia da semana: sábado e domingo têm ROAS 17% maior que quarta e quinta. CPC fim de semana: R$4.38 vs R$5.17 meio da semana."}}]}},
        {"object": "block", "type": "bulleted_list_item",
         "bulleted_list_item": {"rich_text": [{"text": {"content": "Por semana do mês: 1ª semana (dias 1-7) tem ROAS 3.49 | 4ª semana (dias 22-28) tem ROAS 2.95. Accelerar no início, recuar na 4ª semana."}}]}},
        {"object": "block", "type": "divider", "divider": {}},
        {"object": "block", "type": "heading_3",
         "heading_3": {"rich_text": [{"text": {"content": "Resumo do Cenário-Meta"}}]}},
        _mk_table_notion(headers_cenario, rows_cenario),
        {"object": "block", "type": "divider", "divider": {}},
    ]
    return blocos

def _blocos_metas_mes(resumo: dict) -> list[dict]:
    """
    Seção — Onde Estamos — Metas do Mês.
    Tabela expandida: Métrica | Abr/26 | Mai/26 | Jun/26 | Meta Q3 | Var Jun->Meta
    Com dados históricos hardcoded.
    """
    H = _DADOS_HISTORICO
    Q = _META_Q3

    def _var(atual, meta) -> str:
        if atual is None or meta is None or meta == 0:
            return "—"
        v = (meta - atual) / abs(atual) * 100
        sinal = "+" if v > 0 else ""
        return f"{sinal}{v:.1f}%"

    def _brl(v):
        if v is None: return "—"
        return "R$" + f"{int(v):,}".replace(",", ".")

    def _x(v):
        if v is None: return "—"
        return f"{v:.2f}x".replace(".", ",")

    def _pct(v):
        if v is None: return "—"
        return f"{v:.2f}%".replace(".", ",")

    header = ["Métrica", "Abr/26", "Mai/26", "Jun/26", "Meta Q3", "Var Jun→Meta"]
    n = len(header)

    linhas = [
        ["Invest. Meta",
         _brl(H["Abr/26"]["invest_meta"]),
         _brl(H["Mai/26"]["invest_meta"]),
         _brl(H["Jun/26"]["invest_meta"]),
         _brl(Q["invest_meta"]),
         _var(H["Jun/26"]["invest_meta"], Q["invest_meta"])],

        ["Receita Meta",
         _brl(H["Abr/26"]["receita_meta"]),
         _brl(H["Mai/26"]["receita_meta"]),
         _brl(H["Jun/26"]["receita_meta"]),
         _brl(Q["receita_meta"]),
         _var(H["Jun/26"]["receita_meta"], Q["receita_meta"])],

        ["Compras Meta",
         f"{H['Abr/26']['compras_meta']:,}".replace(",", "."),
         f"{H['Mai/26']['compras_meta']:,}".replace(",", "."),
         f"{H['Jun/26']['compras_meta']:,}".replace(",", "."),
         f"{Q['compras_meta']:,}".replace(",", "."),
         _var(H["Jun/26"]["compras_meta"], Q["compras_meta"])],

        ["Ticket Médio Meta",
         _brl(H["Abr/26"]["ticket_meta"]),
         _brl(H["Mai/26"]["ticket_meta"]),
         _brl(H["Jun/26"]["ticket_meta"]),
         _brl(Q["ticket_meta"]),
         _var(H["Jun/26"]["ticket_meta"], Q["ticket_meta"])],

        ["ROAS Meta",
         _x(H["Abr/26"]["roas_meta"]),
         _x(H["Mai/26"]["roas_meta"]),
         _x(H["Jun/26"]["roas_meta"]),
         _x(Q["roas_meta"]),
         _var(H["Jun/26"]["roas_meta"], Q["roas_meta"])],

        ["Invest. Google",
         _brl(H["Abr/26"]["invest_google"]),
         _brl(H["Mai/26"]["invest_google"]),
         _brl(H["Jun/26"]["invest_google"]),
         "—", "—"],

        ["Receita Google",
         _brl(H["Abr/26"]["receita_google"]),
         _brl(H["Mai/26"]["receita_google"]),
         _brl(H["Jun/26"]["receita_google"]),
         "—", "—"],

        ["ROAS Google",
         _x(H["Abr/26"]["roas_google"]),
         _x(H["Mai/26"]["roas_google"]),
         _x(H["Jun/26"]["roas_google"]),
         "—", "—"],

        ["Frete Cobrado",
         _brl(H["Abr/26"]["frete"]),
         _brl(H["Mai/26"]["frete"]),
         _brl(H["Jun/26"]["frete"]),
         "—", "—"],

        ["Taxa de Descontos",
         _pct(H["Abr/26"]["taxa_desconto"]),
         _pct(H["Mai/26"]["taxa_desconto"]),
         _pct(H["Jun/26"]["taxa_desconto"]),
         "—", "—"],

        ["Novos Clientes",
         f"{H['Abr/26']['novos_clientes']:,}".replace(",", "."),
         f"{H['Mai/26']['novos_clientes']:,}".replace(",", "."),
         f"{H['Jun/26']['novos_clientes']:,}".replace(",", "."),
         "—", "—"],

        ["CAC",
         "R$" + f"{H['Abr/26']['cac']:.2f}".replace(".", ","),
         "R$" + f"{H['Mai/26']['cac']:.2f}".replace(".", ","),
         "R$" + f"{H['Jun/26']['cac']:.2f}".replace(".", ","),
         "—", "—"],
    ]

    header_row = {
        "type": "table_row",
        "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}
    }
    data_rows = []
    for linha in linhas:
        data_rows.append({
            "type": "table_row",
            "table_row": {"cells": [[{"text": {"content": c}}] for c in linha]}
        })

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🎯 Onde Estamos — Metas"}}]}},
        {
            "object": "block", "type": "table", "table": {
                "table_width": n,
                "has_column_header": True,
                "has_row_header": False,
                "children": [header_row] + data_rows,
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
    Evolução mensal desde Jan/2026 até o mês anterior a data_ref.
    Colunas: Mês | Invest. Meta | Invest.+Imposto | Receita Meta | Receita Shopify | MER | ROAS | Compras
    Dados Meta: SQLite. Receita Shopify: Shopify API (com fallback para —).
    """
    from calendar import monthrange

    # Sempre de Jan/2026 até o mês anterior a data_ref
    ano_ini, mes_ini = 2026, 1
    ano_fim, mes_fim = data_ref.year, data_ref.month
    mes_fim -= 1
    if mes_fim == 0:
        mes_fim = 12
        ano_fim -= 1

    registros = []
    ano, mes = ano_ini, mes_ini
    while (ano, mes) <= (ano_fim, mes_fim):
        _, ultimo_dia = monthrange(ano, mes)
        ini = date(ano, mes, 1)
        fim = date(ano, mes, ultimo_dia)
        registros.append((ano, mes, ini, fim))
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1

    if not registros:
        return []

    # Busca receita Shopify em paralelo para todos os meses (reduz latência N*serial → ~1 chamada paralela)
    shopify_receitas: dict[tuple, float] = {}
    try:
        from shopify.client import receita_total as _shopify_receita
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def _fetch_shopify(ini_fim):
            ini_m, fim_m = ini_fim
            return ini_fim, _shopify_receita(ini_m, fim_m)
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(_fetch_shopify, (ini, fim)): (ini, fim) for _, _, ini, fim in registros}
            for fut in as_completed(futures):
                try:
                    key, val = fut.result()
                    shopify_receitas[key] = val
                except Exception:
                    pass
    except Exception:
        pass  # Shopify indisponível — colunas ficarão com —

    conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()

    serie = []
    for ano_m, mes_m, ini, fim in registros:
        cur.execute("""
            SELECT
                SUM(investimento)              AS invest,
                SUM(COALESCE(compras,0))       AS compras,
                SUM(COALESCE(valor_compras,0)) AS receita_meta
            FROM campanhas
            WHERE data_inicio >= ? AND data_fim <= ?
        """, (ini.isoformat(), fim.isoformat()))
        row          = cur.fetchone()
        invest       = float(row["invest"] or 0)
        compras      = int(row["compras"] or 0)
        receita_meta = float(row["receita_meta"] or 0)
        invest_imp   = spend_com_imposto(invest)
        roas         = round(receita_meta / invest, 2) if invest > 0 else None

        receita_shopify = shopify_receitas.get((ini, fim))
        invest_total    = invest_imp  # Google: 0 por ora
        mer = round(receita_shopify / invest_total, 2) if (receita_shopify and invest_total > 0) else None

        serie.append({
            "mes_label":       f"{_MESES_PT[mes_m]}/{ano_m}",
            "invest":          invest,
            "invest_imp":      invest_imp,
            "receita_meta":    receita_meta,
            "receita_shopify": receita_shopify,
            "mer":             mer,
            "roas":            roas,
            "compras":         compras,
        })
    conn.close()

    if not any(s["invest"] > 0 for s in serie):
        return []

    header = ["Mês", "Invest. Meta", "Invest.+Imposto", "Receita Meta", "Receita Shopify", "MER", "ROAS", "Compras"]
    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}}
    ]
    for s in serie:
        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": s["mes_label"]}}],
            [{"text": {"content": _fmt_brl(s["invest"])}}],
            [{"text": {"content": _fmt_brl(s["invest_imp"])}}],
            [{"text": {"content": _fmt_brl(s["receita_meta"])}}],
            [{"text": {"content": _fmt_brl(s["receita_shopify"]) if s["receita_shopify"] is not None else "—"}}],
            [{"text": {"content": _fmt_x(s["mer"]) if s["mer"] is not None else "—"}}],
            [{"text": {"content": _fmt_x(s["roas"])}}],
            [{"text": {"content": str(s["compras"]) if s["compras"] else "—"}}],
        ]}})

    n_meses = len(serie)
    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": f"📅 Evolução Mensal — Jan/2026 ({n_meses} meses)"}}]}},
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
    # ── Atualiza dados da planilha Google Sheets antes de gerar ──────────────────
    import os as _os_builder
    if _os_builder.environ.get("SKIP_SHEETS_SYNC") == "1":
        logger.info("[Builder] Sync já realizado pelo cron — pulando carregar_sheets()")
    else:
        try:
            from db.legacy.loader_sheets import carregar_sheets
            linhas, periodo = carregar_sheets(db_path=db_path)
            logger.info("[Builder] Planilha atualizada: %d linhas | período: %s", linhas, periodo)
        except Exception as _e:
            logger.warning("[Builder] Atualização da planilha falhou: %s — usando dados locais", _e)


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

    # ── Receita Shopify (real) para o período atual e anterior ───────────────
    try:
        import sqlite3 as _sq
        _conn = _sq.connect(str(db_path))
        def _shopify_receita_periodo(ini, fim):
            row = _conn.execute(
                "SELECT COALESCE(SUM(receita),0) AS r FROM shopify_receita_diaria "
                "WHERE data >= ? AND data <= ?",
                (str(ini), str(fim))
            ).fetchone()
            return float(row[0]) if row and row[0] else None
        resumo["receita_shopify"] = _shopify_receita_periodo(data_inicio, data_fim)
        _receita_shopify_ant       = _shopify_receita_periodo(ant_inicio, ant_fim)
        _conn.close()
        logger.info("[Builder] Receita Shopify atual=%s anterior=%s",
                    resumo['receita_shopify'], _receita_shopify_ant)
    except Exception as _e:
        logger.warning("[Builder] Falha ao buscar receita Shopify: %s", _e)
        resumo["receita_shopify"] = None
        _receita_shopify_ant = None

    # ── Dados período anterior ─────────────────────────────────────────────
    campanhas_anterior = _buscar_campanhas(ant_inicio, ant_fim, db_path)
    resumo_anterior    = _resumo_geral(campanhas_anterior) if campanhas_anterior else {}
    resumo_anterior["receita_shopify"] = _receita_shopify_ant

    alertas = _alertas(campanhas, resumo, resumo_anterior=resumo_anterior)

    logger.info("[Builder] %d campanhas · Invest.: %s · ROAS: %s",
                len(campanhas), _fmt_brl(resumo["total_invest"]), _fmt_x(resumo["roas"]))

    # ── Blocos Notion ──────────────────────────────────────────────────────
    if tipo == "mensal":
        mes_pt  = _MESES_PT[data_inicio.month]
        titulo  = f"Source Of Report - Mensal {mes_pt} {data_inicio.year}"
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
        # ── Estrutura do relatório mensal ────────────────────────────────────
        # Última semana do mês (igual ao semanal) + consolidado do mês
        blocos = [callout_gerado]

        # Aviso de qualidade de dados — apenas ERRORs reais (warnings operacionais omitidos)
        if val_report and not val_report.ok:
            errors_txt = "\n".join(
                f"❌ {i.check}: {i.message}"
                for i in val_report.issues
                if i.severity == 'ERROR'
            )
            if errors_txt:
                blocos.append({
                    "object": "block", "type": "callout",
                    "callout": {
                        "rich_text": [{"text": {"content": f"⚠️ Qualidade dos dados\n{errors_txt}"}}],
                        "icon": {"type": "emoji", "emoji": "⚠️"},
                        "color": "yellow_background",
                    }
                })

        # ── Dados Shopify — última semana do mês ─────────────────────────
        sem_fim = data_fim
        sem_ini = data_fim - timedelta(days=6)
        try:
            from shopify.client import (
                pedidos_por_sku      as _shopify_sku_m,
                pedidos_por_estado   as _shopify_estado_m,
                recompras_por_sku    as _shopify_recompras_m,
                assiduidade_clientes as _shopify_assiduidade_m,
                metricas_site        as _shopify_metricas_m,
            )
            logger.info('[Builder] Shopify mensal — última semana %s → %s', sem_ini, sem_fim)
            dados_sku_m         = _shopify_sku_m(sem_ini, sem_fim)
            dados_estado_m      = _shopify_estado_m(sem_ini, sem_fim)
            dados_recompras_m   = _shopify_recompras_m(sem_ini, sem_fim)
            dados_assiduidade_m = _shopify_assiduidade_m(sem_ini, sem_fim)
            dados_metricas_m    = _shopify_metricas_m(sem_ini, sem_fim)
            logger.info('[Builder] Shopify mensal OK — %d SKUs, %d estados', len(dados_sku_m), len(dados_estado_m))
        except Exception as _e:
            logger.warning('[Builder] Shopify mensal indisponível: %s — placeholder', _e)
            dados_sku_m = dados_estado_m = dados_recompras_m = dados_assiduidade_m = dados_metricas_m = None

        # Resumo da última semana (Meta) para seções semanais
        _campanhas_sem = _buscar_campanhas(sem_ini, sem_fim, db_path)
        resumo_sem     = _resumo_geral(_campanhas_sem) if _campanhas_sem else resumo

        # ════════════════════════════════════════════════════════════════
        # PARTE 1 — ANÁLISE SEMANAL (última semana do mês)
        # ════════════════════════════════════════════════════════════════
        blocos.append({"object": "block", "type": "heading_1",
            "heading_1": {"rich_text": [{"text": {"content":
                f"📋 Análise Semanal — {sem_ini.strftime('%d/%m')} a {sem_fim.strftime('%d/%m/%Y')}"
            }}]}})

        # S1. Funil de Conversão
        blocos += _blocos_funil(resumo_sem)

        # S2. Métricas de Site (última semana)
        blocos += _blocos_metricas_site_semanal(
            data_inicio=sem_ini,
            data_fim=sem_fim,
            shopify=dados_metricas_m,
            invest_meta=resumo_sem.get("total_invest"),
        )

        # S3. Alocação Budget por Canal
        blocos += _blocos_alocacao_canais(resumo_sem)

        # S4. Alocação Budget por Anúncio
        blocos += _blocos_alocacao_por_anuncio(sem_ini, sem_fim, db_path)

        # S5. Pedidos por SKU (última semana)
        blocos += _blocos_pedidos_sku(dados_sku_m)

        # S6. Pedidos por Estado (última semana)
        blocos += _blocos_pedidos_estado(dados_estado_m)

        # S7. Recuperação de Carrinho (placeholder)
        blocos += _blocos_placeholder(
            "Recuperação de Carrinho Abandonado", "🔄",
            "⏳ Dados de recuperação de carrinho pendentes.",
        )

        # S8. Pedidos Orgânicos Instagram (placeholder)
        blocos += _blocos_placeholder(
            "Pedidos Orgânicos Instagram", "📸",
            "⏳ Dados de pedidos orgânicos pendentes.",
        )

        # S9. Base de Recompradoras (última semana)
        blocos += _blocos_recompradoras(
            dados_recompras_sku=dados_recompras_m,
            dados_assiduidade=dados_assiduidade_m,
        )

        # S10. Comparativo com semana anterior
        if resumo_anterior:
            blocos += _blocos_comparativo(resumo_sem, resumo_anterior, periodo_anterior_str)

        # ════════════════════════════════════════════════════════════════
        # PARTE 2 — ANÁLISE MENSAL (consolidado do mês)
        # ════════════════════════════════════════════════════════════════
        blocos.append({"object": "block", "type": "heading_1",
            "heading_1": {"rich_text": [{"text": {"content":
                f"📅 Análise Mensal — {_MESES_PT[data_inicio.month]}/{data_inicio.year}"
            }}]}})

        # M1. Visão Mensal (narrativa + 3 tabelas diárias)
        _dados_diarios_mes = _buscar_dados_diarios(data_inicio, data_fim, db_path)
        _nome_mes_pt = _MESES_PT[data_inicio.month]
        blocos += _blocos_visao_mensal_narrativa(resumo, _dados_diarios_mes, _nome_mes_pt, data_inicio.year)
        blocos += _blocos_visao_mensal_diaria(data_inicio, data_fim, db_path, dados_diarios=_dados_diarios_mes)

        # M2. Análise da última semana do mês (comparativo semana a semana)
        blocos += _blocos_analise_ultima_semana(data_inicio, data_fim, db_path)

        # M3. Evolução Mensal Jan/2026
        blocos += _blocos_tendencia_mensal(data_inicio, meses=4, db_path=db_path)

        # M4. Metas do mês
        blocos += _blocos_metas_mes(resumo)

        # M5. Próximos Passos
        blocos += _blocos_proximos_passos()

    else:
        # ── Estrutura do relatório semanal ──────────────────────────────────
        blocos = []

        # Aviso de qualidade de dados — apenas ERRORs reais (warnings operacionais omitidos)
        if val_report and not val_report.ok:
            errors_txt = "\n".join(
                f"❌ {i.check}: {i.message}"
                for i in val_report.issues
                if i.severity == 'ERROR'
            )
            if errors_txt:
                blocos.append({
                    "object": "block", "type": "callout",
                    "callout": {
                        "rich_text": [{"text": {"content": f"⚠️ Qualidade dos dados\n{errors_txt}"}}],
                        "icon": {"type": "emoji", "emoji": "⚠️"},
                        "color": "yellow_background",
                    }
                })

        # ── Dados Shopify (reais) ─────────────────────────────────────
        try:
            from shopify.client import (
                pedidos_por_sku      as _shopify_sku,
                pedidos_por_estado   as _shopify_estado,
                recompras_por_sku    as _shopify_recompras,
                assiduidade_clientes as _shopify_assiduidade,
                metricas_site        as _shopify_metricas_site,
            )
            logger.info('[Builder] Buscando dados Shopify %s → %s', data_inicio, data_fim)
            dados_sku            = _shopify_sku(data_inicio, data_fim)
            dados_estado         = _shopify_estado(data_inicio, data_fim)
            dados_recompras      = _shopify_recompras(data_inicio, data_fim)
            dados_assiduidade    = _shopify_assiduidade(data_inicio, data_fim)
            dados_metricas_site  = _shopify_metricas_site(data_inicio, data_fim)
            logger.info('[Builder] Shopify OK — %d SKUs, %d estados', len(dados_sku), len(dados_estado))
        except Exception as _e:
            logger.warning('[Builder] Shopify indisponível: %s — usando placeholder', _e)
            dados_sku = dados_estado = dados_recompras = dados_assiduidade = dados_metricas_site = None

        # 1. Funil de Conversão
        blocos += _blocos_funil(resumo)

        # 2. Métricas de Site (Shopify)
        blocos += _blocos_metricas_site_semanal(
            data_inicio=data_inicio,
            data_fim=data_fim,
            shopify=dados_metricas_site,
            invest_meta=resumo.get('total_invest'),
        )

        # 3. Alocação Budget por Canal
        blocos += _blocos_alocacao_canais(resumo)

        # 4. Alocação Budget por Anúncio
        blocos += _blocos_alocacao(campanhas)

        # 5. Pedidos por SKU
        blocos += _blocos_pedidos_sku(dados_sku)

        # 6. Pedidos por Estado
        blocos += _blocos_pedidos_estado(dados_estado)

        # 7. Recuperação de Carrinho (placeholder)
        blocos += _blocos_placeholder(
            "Recuperação de Carrinho Abandonado", "🔄",
            "⏳ Dados de recuperação de carrinho pendentes.",
        )

        # 8. Pedidos Orgânicos Instagram (placeholder)
        blocos += _blocos_placeholder(
            "Pedidos Orgânicos Instagram", "📸",
            "⏳ Dados de pedidos orgânicos pendentes.",
        )

        # 9. Base de Recompradoras
        blocos += _blocos_recompradoras(
            dados_recompras_sku=dados_recompras,
            dados_assiduidade=dados_assiduidade,
        )

        # 10. Comparativo com Período Anterior
        if resumo_anterior:
            blocos += _blocos_comparativo(resumo, resumo_anterior, periodo_anterior_str)

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
