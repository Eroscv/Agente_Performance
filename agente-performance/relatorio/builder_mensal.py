"""
relatorio/builder_mensal.py

Gerador de relatório mensal Meta Ads.
Helpers exclusivos para análise mensal + função pública gerar_relatorio_mensal.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from relatorio.builder_shared import (
    # constantes
    CLIENTES, DB_PATH, _SQLITE_TIMEOUT, _MESES_PT, _MESES_PT_ABR,
    # db
    _buscar_campanhas, _resumo_geral, _alertas, _buscar_dados_diarios,
    # dados históricos
    _DADOS_HISTORICO, _META_Q3, _RECOMPRAS_HISTORICO, _RECOMPRAS_ESTADOS,
    # fmt
    _fmt_brl, _fmt_x, _fmt_pct, _fmt_n, _fmt_data_pt, _mk_table_notion,
    # blocos compartilhados
    _blocos_resumo, _blocos_tabela_campanhas, _blocos_top_campanhas,
    _blocos_alocacao, _blocos_alertas, _blocos_comparativo, _blocos_funil,
    _blocos_metricas_site_semanal, _blocos_alocacao_canais, _blocos_alocacao_por_anuncio,
    _blocos_placeholder, _blocos_pedidos_sku, _blocos_pedidos_estado, _blocos_recompradoras,
)
from relatorio.regras import spend_com_imposto, calcular_cpa

logger = logging.getLogger(__name__)

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
    # Import local para evitar circular: builder_semanal importa de builder_mensal
    from relatorio.builder_semanal import gerar_relatorio_semanal

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
