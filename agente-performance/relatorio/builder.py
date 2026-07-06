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
    conn = sqlite3.connect(db_path)
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
    """Bloco de resumo executivo."""
    r = resumo
    blocos = [
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content": f"Relatório gerado automaticamente pelo Agente de Performance Guará · {periodo_str}"}}],
                "icon": {"type": "emoji", "emoji": "⚙️"},
            }
        },
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📊 Resumo Geral da Conta"}}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"text": {"content":
            f"💰 Investimento Meta Ads: {_fmt_brl(r['total_invest'])}  |  "
            f"Invest. + Imposto ({FATOR_IMPOSTO}×): {_fmt_brl(r['invest_imp'])}"
         }}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"text": {"content":
            f"👁 Impressões: {_fmt_n(r['total_impressoes'])}  |  "
            f"🖱 Cliques: {_fmt_n(r['total_cliques'])}  |  "
            f"📊 CTR: {_fmt_pct(r['ctr'])}  |  "
            f"💵 CPC médio: {_fmt_brl(r['cpc_medio'])}"
         }}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"text": {"content":
            f"🛒 Compras: {r['total_compras']}  |  "
            f"💰 CPA Meta: {_fmt_brl(r.get('cpa_meta'))}  |  "
            f"💰 CPA Ajustado (+imp.): {_fmt_brl(r.get('cpa_ajustado'))}  |  "
            f"📈 ROAS: {_fmt_x(r['roas'])}  |  "
            f"📦 CPM: {_fmt_brl(r['cpm_medio'])}"
         }}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"text": {"content":
            f"💵 Receita Total: {_fmt_brl(r['total_receita'])}  |  "
            f"  Própria: {_fmt_brl(r.get('receita_propria'))}  |  "
            f"  Atribuída (cross): {_fmt_brl(r.get('receita_atribuida'))}  |  "
            f"🎫 Ticket Médio: {_fmt_brl(r.get('ticket_medio'))}"
         }}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"text": {"content":
            f"🛒 Adições ao Carrinho: {_fmt_n(r['total_carrinho'])}  |  "
            f"💳 Checkouts Iniciados: {_fmt_n(r['total_checkout'])}  |  "
            f"🎯 Taxa Conv. (click→compra): {_fmt_pct(r['conv_rate'])}  |  "
            f"🔄 Frequência Média: {_fmt_n(r.get('frequencia_media'), 2)}"
         }}]}},
        {"object": "block", "type": "divider", "divider": {}},
    ]
    return blocos


def _blocos_tabela_campanhas(rows: list[dict]) -> list[dict]:
    """Tabela principal de campanhas."""
    if not rows:
        return []

    header = ["Campanha", "Invest.", "Invest.+Imp.", "Compras", "CPA Meta", "CPA Ajust.", "Ticket Médio", "ROAS", "CPC", "CTR", "Impressões"]
    table_rows = []

    # Cabeçalho
    table_rows.append({
        "type": "table_row",
        "table_row": {"cells": [[{"text": {"content": h}, "annotations": {"bold": True}}] for h in header]}
    })

    # Dados
    for c in rows:
        invest  = float(c.get("investimento") or 0)
        compras = int(c.get("compras") or 0)
        receita = float(c.get("valor_compras") or 0)
        invest_imp  = spend_com_imposto(invest)
        roas        = round(receita / invest, 4) if invest > 0 else None
        cpa_ajust   = calcular_cpa(invest, compras)
        cpa_meta_v  = float(c.get("custo_por_compra_meta") or 0) or None
        ticket      = round(receita / compras, 2) if compras else None
        cpc  = c.get("cpc_link")
        ctr  = c.get("ctr_link")
        impressoes = int(c.get("impressoes") or 0)

        table_rows.append({
            "type": "table_row",
            "table_row": {"cells": [
                [{"text": {"content": (c.get("campanha") or "")[:60]}}],
                [{"text": {"content": _fmt_brl(invest)}}],
                [{"text": {"content": _fmt_brl(invest_imp)}}],
                [{"text": {"content": str(compras) if compras else "—"}}],
                [{"text": {"content": _fmt_brl(cpa_meta_v)}}],
                [{"text": {"content": _fmt_brl(cpa_ajust)}}],
                [{"text": {"content": _fmt_brl(ticket)}}],
                [{"text": {"content": _fmt_x(roas)}}],
                [{"text": {"content": _fmt_brl(float(cpc)) if cpc else "—"}}],
                [{"text": {"content": _fmt_pct(float(ctr)) if ctr else "—"}}],
                [{"text": {"content": _fmt_n(impressoes)}}],
            ]}
        })

    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "📋 Desempenho por Campanha"}}]}},
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


def _blocos_top_campanhas(campanhas: list[dict]) -> list[dict]:
    """Top 5 campanhas por ROAS."""
    rankeadas = []
    for c in campanhas:
        invest  = float(c.get("investimento") or 0)
        roas    = round(float(c.get('valor_compras') or 0) / float(c.get('investimento') or 1), 4) if float(c.get('investimento') or 0) > 0 else None
        if roas:
            rankeadas.append((c.get("campanha", ""), float(roas), invest))

    rankeadas.sort(key=lambda x: x[1], reverse=True)

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🏆 Top Campanhas por ROAS"}}]}},
    ]
    for i, (nome, roas, invest) in enumerate(rankeadas[:5], 1):
        blocos.append({
            "object": "block", "type": "numbered_list_item",
            "numbered_list_item": {"rich_text": [{"text": {"content":
                f"{nome}  —  ROAS {roas:.2f}x  |  Invest. {_fmt_brl(invest)}"
            }}]}
        })

    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


def _blocos_alocacao(campanhas: list[dict]) -> list[dict]:
    """Budget allocation."""
    total = sum(float(c.get("investimento") or 0) for c in campanhas)
    if total == 0:
        return []

    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "💰 Alocação de Budget"}}]}},
    ]
    for c in sorted(campanhas, key=lambda x: float(x.get("investimento") or 0), reverse=True):
        invest = float(c.get("investimento") or 0)
        pct    = calcular_pct_budget(invest, total)
        blocos.append({
            "object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content":
                f"{c.get('campanha', '')}:  {_fmt_brl(invest)}  ({_fmt_pct(pct)})"
            }}]}
        })

    blocos.append({"object": "block", "type": "divider", "divider": {}})
    return blocos


def _blocos_alertas(alertas: list[str]) -> list[dict]:
    """Bloco de alertas automáticos."""
    emoji = "✅" if len(alertas) == 1 and alertas[0].startswith("✅") else "🔴"
    blocos = [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": "🚨 Alertas Automáticos"}}]}},
    ]
    for a in alertas:
        blocos.append({
            "object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content": a}}]}
        })
    return blocos




def _blocos_comparativo(resumo_atual: dict, resumo_anterior: dict, periodo_anterior_str: str) -> list[dict]:
    """
    Bloco de comparativo entre período atual e anterior.
    Mostra tabela com ROAS, Invest, Receita, Compras, CPA, CTR, CPC
    + variação % e tendência (↑↓→).
    """
    def _variacao_pct(atual, anterior):
        """Retorna (variacao_pct, tendencia_emoji)"""
        if atual is None or anterior is None or anterior == 0:
            return None, "→"
        var = (atual - anterior) / abs(anterior) * 100
        if abs(var) < 5:
            tendencia = "→"
        elif var > 0:
            tendencia = "↑"
        else:
            tendencia = "↓"
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
    ]

    header = ["Métrica", "Atual", "Anterior", "Variação", "Tendência"]
    table_rows = [
        {
            "type": "table_row",
            "table_row": {"cells": [[{"text": {"content": h}, "annotations": {"bold": True}}] for h in header]}
        }
    ]

    for nome, atual, anterior, fmt_fn in metricas:
        var, tend = _variacao_pct(atual, anterior)
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

# ── Função principal ──────────────────────────────────────────────────────────

def gerar_relatorio_semanal(
    cliente: str = "source_of",
    data_inicio: date = None,
    data_fim: date = None,
    publicar: bool = True,
    db_path: Path = DB_PATH,
) -> dict:
    """
    Gera e publica o relatório semanal Meta Ads.

    Args:
        cliente:      chave do cliente (ex: 'source_of')
        data_inicio:  início do período (default: segunda-feira da semana passada)
        data_fim:     fim do período (default: domingo da semana passada)
        publicar:     se True, publica no Notion
        db_path:      caminho do SQLite

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

    periodo_str = f"{data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"
    cliente_nome = CLIENTES.get(cliente, cliente)

    # ── Período anterior (mesma duração) ──────────────────────────────────
    delta_dias = (data_fim - data_inicio).days + 1
    ant_fim    = data_inicio - timedelta(days=1)
    ant_inicio = ant_fim - timedelta(days=delta_dias - 1)
    periodo_anterior_str = f"{ant_inicio.strftime('%d/%m/%Y')} → {ant_fim.strftime('%d/%m/%Y')}"

    logger.info("[Builder] Gerando relatório %s · %s", cliente_nome, periodo_str)

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
    titulo = f"📊 {cliente_nome} · Meta Ads · {periodo_str}"

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
    # Comparativo com período anterior (se tiver dados)
    if resumo_anterior:
        blocos += _blocos_comparativo(resumo, resumo_anterior, periodo_anterior_str)
    blocos += _blocos_tabela_campanhas(campanhas)
    blocos += _blocos_top_campanhas(campanhas)
    blocos += _blocos_alocacao(campanhas)
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

    # Detecta tipo (semanal vs mensal) pelo delta de dias
    delta = (data_fim - data_inicio).days
    tipo  = "mensal" if delta >= 25 else "semanal"

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
