"""
relatorio/shared/mensal.py

Blocos específicos do relatório mensal: SKUs, estados, recompradoras, narrativa.
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
from relatorio.shared.blocos import (
    _mk_table_notion,
    _RECOMPRAS_HISTORICO,
    _RECOMPRAS_ESTADOS,
    _DADOS_HISTORICO,
    _MESES_PT_ABR,
)
from relatorio.shared.constants import _SQLITE_TIMEOUT

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



# ── Helpers mensais (compartilhados com semanal) ─────────────────────────────


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
    from relatorio.builder_mensal import _gerar_texto_narrativo_mensal
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





def _blocos_metas_mes(resumo: dict) -> list[dict]:
    """
    Seção — Onde Estamos — Metas do Mês.
    Tabela expandida: Métrica | Abr/26 | Mai/26 | Jun/26 | Meta Q3 | Var Jun->Meta
    Com dados históricos hardcoded.
    """
    H = _DADOS_HISTORICO
    Q = _META_Q3


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


