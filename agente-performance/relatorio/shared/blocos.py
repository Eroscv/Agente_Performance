"""
relatorio/shared/blocos.py

Builders de blocos Notion: resumo, tabelas, alertas, funil, alocação.
"""
import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from db.connections import LEGACY_DB_PATH as DB_PATH
from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n
from notion.blocks import h2, h3, paragrafo, divider, callout, bullet, toggle, codigo, tabela

logger = logging.getLogger(__name__)

from relatorio.shared.formatters import _fmt_brl, _fmt_x, _fmt_pct, _fmt_n, _fmt_data_pt
from relatorio.shared.constants import _SQLITE_TIMEOUT

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

    def _delta(atual, ant, menor_melhor=False):
        try:
            a, b = float(atual or 0), float(ant or 0)
            if b == 0: return ""
            pct = (a - b) / abs(b) * 100
            up = pct > 0
            ok = (up and not menor_melhor) or (not up and menor_melhor)
            seta = "↑" if up else "↓"
            emoji = "✅" if ok else "⚠️"
            return f"{seta} {abs(pct):.1f}% {emoji}"
        except: return ""

    metricas = [
        ("Invest.",  resumo_atual.get("investimento"),   resumo_anterior.get("investimento"),  True),
        ("Receita",  resumo_atual.get("total_receita"),   resumo_anterior.get("total_receita"),  False),
        ("ROAS",     resumo_atual.get("roas"),            resumo_anterior.get("roas"),           False),
        ("CPA",      resumo_atual.get("cpa_ajustado"),    resumo_anterior.get("cpa_ajustado"),   True),
        ("Compras",  resumo_atual.get("total_compras"),   resumo_anterior.get("total_compras"),  False),
    ]

    from notion.blocks import h2, paragrafo, tabela, divider
    header = ["Métrica", "Atual", periodo_anterior_str or "Anterior", "Variação"]
    rows = []
    for nome, atual, ant, menor_melhor in metricas:
        if atual is None and ant is None: continue
        atual_str = _fmt_brl(atual) if "invest" in nome.lower() or nome in ("Receita", "CPA") else (_fmt_x(atual) if nome == "ROAS" else str(int(atual or 0)))
        ant_str   = _fmt_brl(ant)   if "invest" in nome.lower() or nome in ("Receita", "CPA") else (_fmt_x(ant)   if nome == "ROAS" else str(int(ant or 0)))
        rows.append([nome, atual_str, ant_str, _delta(atual, ant, menor_melhor)])

    if not rows:
        return []

    return [
        h2("🔄 Comparativo vs Período Anterior"),
        _mk_table_notion(header, rows),
        divider(),
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



