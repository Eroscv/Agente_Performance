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
) -> list[dict]:
    """
    Retorna campanhas agregadas por nome no período, apenas com investimento > 0.
    """
    sql = """
        SELECT
            campanha,
            SUM(investimento)          AS investimento,
            SUM(compras)               AS compras,
            SUM(impressoes)            AS impressoes,
            SUM(cliques_link)          AS cliques_link,
            SUM(cliques_todos)         AS cliques_todos,
            AVG(cpc_link)              AS cpc_link,
            AVG(cpm)                   AS cpm,
            SUM(valor_compras)         AS valor_compras,
            SUM(adicoes_carrinho)      AS adicoes_carrinho,
            SUM(checkout_iniciado)     AS checkout_iniciado,
            SUM(alcance)               AS alcance,
            AVG(ctr_link)              AS ctr_link,
            AVG(frequencia)            AS frequencia,
            AVG(roas_compras)          AS roas_compras
        FROM campanhas
        WHERE data_inicio >= ?
          AND data_fim    <= ?
          AND investimento > 0
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

    invest_imp    = spend_com_imposto(total_invest)
    # ROAS: média ponderada pelo investimento dos roas_compras do banco — sem recalcular
    roas_vals = [(float(c.get("roas_compras") or 0), float(c.get("investimento") or 0))
                 for c in campanhas if c.get("roas_compras") is not None]
    if roas_vals and total_invest > 0:
        roas = round(sum(r * w for r, w in roas_vals) / sum(w for _, w in roas_vals), 4)
    else:
        roas = None
    # Guard: total_compras pode ser 0 quando nenhuma campanha do período tem compras
    if total_compras == 0:
        logger.warning("[_resumo_geral] total_compras=0 no período — CPA será None")
    cpa       = calcular_cpa(total_invest, total_compras) if total_compras else None
    ctr           = (total_cliques / total_impressoes * 100) if total_impressoes else None
    cpc_medio     = (total_invest / total_cliques) if total_cliques else None
    cpm_medio     = (total_invest / total_impressoes * 1000) if total_impressoes else None
    conv_rate     = (total_compras / total_cliques * 100) if total_cliques else None

    return {
        "total_invest":     total_invest,
        "invest_imp":       invest_imp,
        "total_compras":    total_compras,
        "total_impressoes": total_impressoes,
        "total_cliques":    total_cliques,
        "total_receita":    total_receita,
        "total_carrinho":   total_carrinho,
        "total_checkout":   total_checkout,
        "total_alcance":    total_alcance,
        "roas":             roas,
        "cpa":              cpa,
        "ctr":              ctr,
        "cpc_medio":        cpc_medio,
        "cpm_medio":        cpm_medio,
        "conv_rate":        conv_rate,
        "num_campanhas":    len(campanhas),
    }


def _alertas(campanhas: list[dict], resumo: dict) -> list[str]:
    """Gera lista de alertas automáticos baseada nos dados."""
    alertas = []

    # Campanhas sem conversão com investimento relevante
    sem_conv = [
        c["campanha"] for c in campanhas
        if (c.get("compras") or 0) == 0 and float(c.get("investimento") or 0) > 200
    ]
    if sem_conv:
        alertas.append(f"⚠️ {len(sem_conv)} campanha(s) com investimento > R$200 sem nenhuma compra: {', '.join(sem_conv[:3])}")

    # ROAS abaixo de 1
    if resumo["roas"] and resumo["roas"] < 1:
        alertas.append(f"🔴 ROAS geral abaixo de 1x ({resumo['roas']:.2f}x) — investimento não está se pagando")
    elif resumo["roas"] and resumo["roas"] < 2:
        alertas.append(f"🟡 ROAS geral abaixo de 2x ({resumo['roas']:.2f}x) — atenção à rentabilidade")

    # CTR baixo
    if resumo["ctr"] and resumo["ctr"] < 1:
        alertas.append(f"⚠️ CTR médio baixo ({resumo['ctr']:.2f}%) — criativos podem precisar de revisão")

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
    return [
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
            f"💰 CPA: {_fmt_brl(r['cpa'])}  |  "
            f"📈 ROAS: {_fmt_x(r['roas'])}  |  "
            f"📦 CPM: {_fmt_brl(r['cpm_medio'])}"
         }}]}},
        {"object": "block", "type": "paragraph",
         "paragraph": {"rich_text": [{"text": {"content":
            f"🛒 Adições ao Carrinho: {_fmt_n(r['total_carrinho'])}  |  "
            f"💳 Checkouts Iniciados: {_fmt_n(r['total_checkout'])}  |  "
            f"🎯 Taxa Conv. (click→compra): {_fmt_pct(r['conv_rate'])}"
         }}]}},
        {"object": "block", "type": "divider", "divider": {}},
    ]


def _blocos_tabela_campanhas(rows: list[dict]) -> list[dict]:
    """Tabela principal de campanhas."""
    if not rows:
        return []

    header = ["Campanha", "Invest.", "Invest.+Imp.", "Compras", "CPA", "ROAS", "CPC", "CTR", "Impressões"]
    table_rows = []

    # Cabeçalho
    table_rows.append({
        "type": "table_row",
        "table_row": {"cells": [[{"text": {"content": h}, "annotations": {"bold": True}}] for h in header]}
    })

    # Dados
    for c in rows:
        invest = float(c.get("investimento") or 0)
        compras = int(c.get("compras") or 0)
        invest_imp = spend_com_imposto(invest)
        roas = c.get("roas_compras")   # direto do banco — sem recalcular
        cpa  = calcular_cpa(invest, compras)
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
                [{"text": {"content": _fmt_brl(cpa)}}],
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
        roas    = c.get("roas_compras")   # direto do banco — sem recalcular
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
    alertas = _alertas(campanhas, resumo)

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
