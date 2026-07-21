"""
relatorio/builder_pod.py

⚠️  CLIENTE: POD KOMBUCHA — dados Google Ads exclusivos.
    NUNCA misturar com dados da Source Of ou qualquer outro cliente.

Gera relatórios semanal e mensal para POD Kombucha com dados do Google Ads.
"""

import logging
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"
_SQLITE_TIMEOUT = 30

CLIENTE_NOME = "POD Kombucha"

_MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março",    4: "Abril",
    5: "Maio",    6: "Junho",     7: "Julho",     8: "Agosto",
    9: "Setembro",10: "Outubro",  11: "Novembro", 12: "Dezembro",
}

# ── Formatadores ─────────────────────────────────────────────────────────────

def _fmt_brl(v) -> str:
    if v is None: return "—"
    try: return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except: return "—"

def _fmt_x(v) -> str:
    if v is None: return "—"
    try: return f"{float(v):.2f}x"
    except: return "—"

def _fmt_n(v, decimais=0) -> str:
    if v is None: return "—"
    try: return f"{float(v):,.{decimais}f}".replace(",", ".")
    except: return "—"

def _fmt_pct(v) -> str:
    if v is None: return "—"
    try: return f"{float(v):.2f}%"
    except: return "—"


# ── Helpers Notion ────────────────────────────────────────────────────────────

def _mk_table(header: list, rows: list) -> dict:
    """Monta bloco de tabela Notion."""
    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}}
    ]
    for row in rows:
        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": str(v)}}] for v in row
        ]}})
    return {
        "object": "block", "type": "table", "table": {
            "table_width": len(header),
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows,
        }
    }

def _h2(txt: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": txt}}]}}

def _h1(txt: str) -> dict:
    return {"object": "block", "type": "heading_1",
            "heading_1": {"rich_text": [{"text": {"content": txt}}]}}

def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}

def _callout(txt: str, emoji: str = "📊", color: str = "gray_background") -> dict:
    return {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content": txt}}],
            "icon": {"type": "emoji", "emoji": emoji},
            "color": color,
        }
    }


# ── Queries SQLite ────────────────────────────────────────────────────────────

def _buscar_periodo(data_inicio: date, data_fim: date) -> dict:
    """Agrega métricas do período da tabela pod_gads_diario."""
    conn = sqlite3.connect(str(DB_PATH), timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            SUM(investimento)     AS invest,
            SUM(cliques)          AS cliques,
            SUM(impressoes)       AS impressoes,
            SUM(conversoes)       AS conversoes,
            SUM(valor_conversao)  AS receita,
            COUNT(DISTINCT campanha) AS num_campanhas
        FROM pod_gads_diario
        WHERE data_inicio >= ? AND data_fim <= ?
    """, (data_inicio.isoformat(), data_fim.isoformat()))
    row = dict(cur.fetchone())
    conn.close()

    invest   = float(row["invest"] or 0)
    cliques  = int(row["cliques"] or 0)
    impr     = int(row["impressoes"] or 0)
    conv     = float(row["conversoes"] or 0)
    receita  = float(row["receita"] or 0)

    roas = round(receita / invest, 2) if invest > 0 else None
    cpc  = round(invest / cliques, 2) if cliques > 0 else None
    ctr  = round(cliques / impr * 100, 2) if impr > 0 else None
    cpa  = round(invest / conv, 2) if conv > 0 else None

    return {
        "invest": invest, "cliques": cliques, "impressoes": impr,
        "conversoes": conv, "receita": receita,
        "roas": roas, "cpc": cpc, "ctr": ctr, "cpa": cpa,
        "num_campanhas": int(row["num_campanhas"] or 0),
    }


def _buscar_campanhas(data_inicio: date, data_fim: date) -> list[dict]:
    """Retorna métricas por campanha no período."""
    conn = sqlite3.connect(str(DB_PATH), timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            campanha,
            tipo_campanha,
            SUM(investimento)    AS invest,
            SUM(cliques)         AS cliques,
            SUM(impressoes)      AS impressoes,
            SUM(conversoes)      AS conversoes,
            SUM(valor_conversao) AS receita
        FROM pod_gads_diario
        WHERE data_inicio >= ? AND data_fim <= ?
        GROUP BY campanha, tipo_campanha
        ORDER BY SUM(investimento) DESC
    """, (data_inicio.isoformat(), data_fim.isoformat()))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    resultado = []
    for r in rows:
        invest  = float(r["invest"] or 0)
        cliques = int(r["cliques"] or 0)
        conv    = float(r["conversoes"] or 0)
        receita = float(r["receita"] or 0)
        resultado.append({
            **r,
            "invest":  invest,
            "cliques": cliques,
            "conversoes": conv,
            "receita": receita,
            "roas": round(receita / invest, 2) if invest > 0 else None,
            "cpa":  round(invest / conv, 2) if conv > 0 else None,
            "cpc":  round(invest / cliques, 2) if cliques > 0 else None,
        })
    return resultado


def _buscar_diario(data_inicio: date, data_fim: date) -> list[dict]:
    """Retorna métricas diárias agregadas."""
    conn = sqlite3.connect(str(DB_PATH), timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            data_inicio AS dia,
            SUM(investimento)    AS invest,
            SUM(cliques)         AS cliques,
            SUM(impressoes)      AS impressoes,
            SUM(conversoes)      AS conversoes,
            SUM(valor_conversao) AS receita
        FROM pod_gads_diario
        WHERE data_inicio >= ? AND data_fim <= ?
        GROUP BY data_inicio
        ORDER BY data_inicio
    """, (data_inicio.isoformat(), data_fim.isoformat()))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Blocos de conteúdo ────────────────────────────────────────────────────────

def _blocos_resumo(resumo: dict, periodo_str: str) -> list[dict]:
    """Callout com resumo executivo do período."""
    invest  = resumo["invest"]
    roas    = resumo["roas"]
    conv    = resumo["conversoes"]
    receita = resumo["receita"]
    cpa     = resumo["cpa"]
    cpc     = resumo["cpc"]
    ctr     = resumo["ctr"]

    txt = (
        f"No período {periodo_str}, o investimento em Google Ads foi de {_fmt_brl(invest)}, "
        f"com ROAS de {_fmt_x(roas)} e {_fmt_n(conv)} conversões. "
        f"Receita atribuída: {_fmt_brl(receita)}. "
        f"CPA: {_fmt_brl(cpa)} | CPC: {_fmt_brl(cpc)} | CTR: {_fmt_pct(ctr)}."
    )
    return [_callout(txt, "📊"), _divider()]


def _blocos_funil(resumo: dict) -> list[dict]:
    """Tabela de funil: Impressões → Cliques → Conversões."""
    impr  = resumo["impressoes"]
    clic  = resumo["cliques"]
    conv  = resumo["conversoes"]
    ctr   = resumo["ctr"]
    conv_r = round(conv / clic * 100, 2) if clic > 0 else None

    header = ["Etapa", "Volume", "Taxa"]
    rows = [
        ("👁️ Impressões",  _fmt_n(impr),  "—"),
        ("🖱️ Cliques",     _fmt_n(clic),  f"CTR {_fmt_pct(ctr)}"),
        ("✅ Conversões",   _fmt_n(conv),  f"Conv. {_fmt_pct(conv_r)} dos cliques"),
    ]
    return [_h2("🔽 Funil de Conversão"), _mk_table(header, rows), _divider()]


def _blocos_metricas(resumo: dict) -> list[dict]:
    """Tabela de métricas principais."""
    header = ["Métrica", "Valor"]
    rows = [
        ("💰 Investimento",     _fmt_brl(resumo["invest"])),
        ("📈 Receita",          _fmt_brl(resumo["receita"])),
        ("🎯 ROAS",             _fmt_x(resumo["roas"])),
        ("🛍️ Conversões",      _fmt_n(resumo["conversoes"])),
        ("💵 CPA",              _fmt_brl(resumo["cpa"])),
        ("🖱️ CPC",             _fmt_brl(resumo["cpc"])),
        ("📊 CTR",              _fmt_pct(resumo["ctr"])),
        ("👁️ Impressões",      _fmt_n(resumo["impressoes"])),
        ("🔗 Cliques",          _fmt_n(resumo["cliques"])),
    ]
    return [_h2("📊 Métricas Google Ads"), _mk_table(header, rows), _divider()]


def _blocos_campanhas(campanhas: list[dict]) -> list[dict]:
    """Tabela de desempenho por campanha."""
    if not campanhas:
        return [_h2("🎯 Desempenho por Campanha"), _callout("⏳ Sem dados de campanha no período.", "⏳"), _divider()]

    header = ["Campanha", "Tipo", "Investimento", "Conversões", "Receita", "ROAS", "CPA", "CPC"]
    rows = []
    for c in campanhas:
        rows.append([
            c.get("campanha", "—"),
            c.get("tipo_campanha", "—"),
            _fmt_brl(c["invest"]),
            _fmt_n(c["conversoes"]),
            _fmt_brl(c["receita"]),
            _fmt_x(c["roas"]),
            _fmt_brl(c["cpa"]),
            _fmt_brl(c["cpc"]),
        ])

    # Linha total
    tot_invest = sum(c["invest"] for c in campanhas)
    tot_conv   = sum(c["conversoes"] for c in campanhas)
    tot_rec    = sum(c["receita"] for c in campanhas)
    tot_roas   = round(tot_rec / tot_invest, 2) if tot_invest > 0 else None
    tot_cpa    = round(tot_invest / tot_conv, 2) if tot_conv > 0 else None
    rows.append(["TOTAL", "—", _fmt_brl(tot_invest), _fmt_n(tot_conv), _fmt_brl(tot_rec), _fmt_x(tot_roas), _fmt_brl(tot_cpa), "—"])

    return [_h2("🎯 Desempenho por Campanha"), _mk_table(header, rows), _divider()]


def _blocos_visao_diaria(data_inicio: date, data_fim: date) -> list[dict]:
    """Tabela dia a dia."""
    dados = _buscar_diario(data_inicio, data_fim)

    header = ["Data", "Investimento", "Cliques", "Impressões", "Conversões", "Receita", "ROAS"]
    rows = []
    tot = {"invest": 0, "cliques": 0, "impressoes": 0, "conversoes": 0, "receita": 0}

    for d in dados:
        invest  = float(d.get("invest") or 0)
        cliques = int(d.get("cliques") or 0)
        impr    = int(d.get("impressoes") or 0)
        conv    = float(d.get("conversoes") or 0)
        rec     = float(d.get("receita") or 0)
        roas    = round(rec / invest, 2) if invest > 0 else None

        try:
            dia_str = date.fromisoformat(d["dia"]).strftime("%d/%m")
        except Exception:
            dia_str = str(d["dia"])

        rows.append([dia_str, _fmt_brl(invest), _fmt_n(cliques), _fmt_n(impr), _fmt_n(conv), _fmt_brl(rec), _fmt_x(roas)])
        tot["invest"] += invest
        tot["cliques"] += cliques
        tot["impressoes"] += impr
        tot["conversoes"] += conv
        tot["receita"] += rec

    if not rows:
        rows.append(["—", "—", "—", "—", "—", "—", "—"])

    roas_tot = round(tot["receita"] / tot["invest"], 2) if tot["invest"] > 0 else None
    rows.append(["TOTAL", _fmt_brl(tot["invest"]), _fmt_n(tot["cliques"]), _fmt_n(tot["impressoes"]), _fmt_n(tot["conversoes"]), _fmt_brl(tot["receita"]), _fmt_x(roas_tot)])

    return [_h2("📅 Visão Diária"), _mk_table(header, rows), _divider()]


def _blocos_comparativo(resumo: dict, resumo_ant: dict, label_ant: str) -> list[dict]:
    """Tabela comparativa atual vs período anterior."""
    def _delta(atual, ant):
        if atual is None or ant is None or ant == 0: return "—"
        d = (atual - ant) / abs(ant) * 100
        sinal = "▲" if d >= 0 else "▼"
        return f"{sinal} {abs(d):.1f}%"

    header = ["Métrica", "Período Atual", label_ant, "Variação"]
    rows = [
        ("💰 Investimento",  _fmt_brl(resumo["invest"]),    _fmt_brl(resumo_ant["invest"]),    _delta(resumo["invest"],    resumo_ant["invest"])),
        ("📈 Receita",       _fmt_brl(resumo["receita"]),   _fmt_brl(resumo_ant["receita"]),   _delta(resumo["receita"],   resumo_ant["receita"])),
        ("🎯 ROAS",          _fmt_x(resumo["roas"]),        _fmt_x(resumo_ant["roas"]),        _delta(resumo["roas"],      resumo_ant["roas"])),
        ("✅ Conversões",    _fmt_n(resumo["conversoes"]),  _fmt_n(resumo_ant["conversoes"]),  _delta(resumo["conversoes"],resumo_ant["conversoes"])),
        ("💵 CPA",           _fmt_brl(resumo["cpa"]),       _fmt_brl(resumo_ant["cpa"]),       _delta(resumo_ant["cpa"],   resumo["cpa"])),  # invertido: menor CPA = melhor
        ("🖱️ CPC",          _fmt_brl(resumo["cpc"]),       _fmt_brl(resumo_ant["cpc"]),       _delta(resumo_ant["cpc"],   resumo["cpc"])),
        ("📊 CTR",           _fmt_pct(resumo["ctr"]),       _fmt_pct(resumo_ant["ctr"]),       _delta(resumo["ctr"],       resumo_ant["ctr"])),
    ]
    return [_h2("📈 Comparativo com Período Anterior"), _mk_table(header, rows), _divider()]


def _blocos_tendencia_mensal(data_ref: date) -> list[dict]:
    """Evolução mensal desde o início dos dados até o mês anterior."""
    conn = sqlite3.connect(str(DB_PATH), timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT MIN(data_inicio) FROM pod_gads_diario")
    min_data = cur.fetchone()[0]
    conn.close()

    if not min_data:
        return []

    from datetime import date as _date
    inicio = _date.fromisoformat(min_data).replace(day=1)
    fim_ref = data_ref.replace(day=1) - timedelta(days=1)  # último dia do mês anterior
    fim_ref = fim_ref.replace(day=1)  # primeiro dia do mês anterior

    meses = []
    cur_mes = inicio
    while cur_mes <= fim_ref:
        if cur_mes.month == 12:
            prox = cur_mes.replace(year=cur_mes.year + 1, month=1, day=1)
        else:
            prox = cur_mes.replace(month=cur_mes.month + 1, day=1)
        fim_mes = prox - timedelta(days=1)
        meses.append((cur_mes, fim_mes))
        cur_mes = prox

    header = ["Mês", "Investimento", "Cliques", "Conversões", "Receita", "ROAS", "CPA"]
    rows = []
    for ini, fim in meses:
        r = _buscar_periodo(ini, fim)
        if r["invest"] == 0 and r["cliques"] == 0:
            continue
        label = f"{_MESES_PT[ini.month][:3]}/{ini.year}"
        rows.append([
            label,
            _fmt_brl(r["invest"]),
            _fmt_n(r["cliques"]),
            _fmt_n(r["conversoes"]),
            _fmt_brl(r["receita"]),
            _fmt_x(r["roas"]),
            _fmt_brl(r["cpa"]),
        ])

    if not rows:
        return []

    return [_h2(f"📅 Evolução Mensal — Histórico"), _mk_table(header, rows), _divider()]


# ── Pipeline principal ────────────────────────────────────────────────────────

def gerar_relatorio_pod(
    data_inicio: date = None,
    data_fim: date = None,
    tipo: str = "semanal",
    publicar: bool = True,
) -> dict:
    """
    ⚠️  CLIENTE: POD KOMBUCHA — Google Ads exclusivo.

    Args:
        data_inicio: início do período
        data_fim:    fim do período
        tipo:        "semanal" ou "mensal"
        publicar:    se True, publica no Notion

    Returns:
        dict com: page_id, url, resumo, erro
    """
    # ── Atualiza dados da planilha ────────────────────────────────────────
    try:
        from db.loader_pod import carregar_pod
        linhas, periodo = carregar_pod()
        logger.info("[POD] Planilha atualizada: %d linhas | %s", linhas, periodo)
    except Exception as e:
        logger.warning("[POD] Atualização da planilha falhou: %s — usando dados locais", e)

    # ── Período padrão ────────────────────────────────────────────────────
    hoje = date.today()
    if tipo == "mensal":
        if data_inicio is None:
            primeiro_mes_ant = (hoje.replace(day=1) - timedelta(days=1)).replace(day=1)
            data_inicio = primeiro_mes_ant
        if data_fim is None:
            data_fim = hoje.replace(day=1) - timedelta(days=1)
    else:
        if data_inicio is None or data_fim is None:
            seg = hoje - timedelta(days=hoje.weekday() + 7)
            dom = seg + timedelta(days=6)
            data_inicio = data_inicio or seg
            data_fim    = data_fim    or dom

    periodo_str = (
        f"{_MESES_PT[data_inicio.month]}/{data_inicio.year}"
        if tipo == "mensal"
        else f"{data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"
    )

    # ── Título ────────────────────────────────────────────────────────────
    if tipo == "mensal":
        titulo = f"📅 POD Kombucha · Google Ads Mensal · {_MESES_PT[data_inicio.month]}/{data_inicio.year}"
    else:
        titulo = f"📊 POD Kombucha · Google Ads · {data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"

    logger.info("[POD] Gerando relatório: %s · tipo=%s", titulo, tipo)

    # ── Busca dados ───────────────────────────────────────────────────────
    resumo    = _buscar_periodo(data_inicio, data_fim)
    campanhas = _buscar_campanhas(data_inicio, data_fim)

    if resumo["num_campanhas"] == 0:
        logger.warning("[POD] Nenhuma campanha encontrada no período %s → %s", data_inicio, data_fim)
        return {"erro": "Nenhuma campanha encontrada", "titulo": titulo}

    # Período anterior (mesma duração)
    duracao     = (data_fim - data_inicio).days + 1
    ant_fim     = data_inicio - timedelta(days=1)
    ant_inicio  = ant_fim - timedelta(days=duracao - 1)
    resumo_ant  = _buscar_periodo(ant_inicio, ant_fim)
    label_ant   = (
        f"{_MESES_PT[ant_inicio.month]}/{ant_inicio.year}"
        if tipo == "mensal"
        else f"{ant_inicio.strftime('%d/%m')}→{ant_fim.strftime('%d/%m')}"
    )

    # ── Monta blocos ──────────────────────────────────────────────────────
    blocos = []

    # Callout de geração
    blocos.append(_callout(
        f"⚙️ Relatório gerado automaticamente em {date.today().strftime('%d/%m/%Y')} · POD Kombucha · Google Ads",
        "⚙️"
    ))

    if tipo == "mensal":
        # PARTE 1 — Análise da última semana
        sem_fim = data_fim
        sem_ini = data_fim - timedelta(days=6)
        resumo_sem   = _buscar_periodo(sem_ini, sem_fim)
        campanhas_sem = _buscar_campanhas(sem_ini, sem_fim)

        blocos.append(_h1(f"📋 Análise Semanal — {sem_ini.strftime('%d/%m')} a {sem_fim.strftime('%d/%m/%Y')}"))
        blocos += _blocos_resumo(resumo_sem, f"{sem_ini.strftime('%d/%m')} a {sem_fim.strftime('%d/%m/%Y')}")
        blocos += _blocos_funil(resumo_sem)
        blocos += _blocos_metricas(resumo_sem)
        blocos += _blocos_campanhas(campanhas_sem)

        # PARTE 2 — Consolidado mensal
        blocos.append(_h1(f"📅 Análise Mensal — {_MESES_PT[data_inicio.month]}/{data_inicio.year}"))
        blocos += _blocos_resumo(resumo, periodo_str)
        blocos += _blocos_funil(resumo)
        blocos += _blocos_metricas(resumo)
        blocos += _blocos_campanhas(campanhas)
        blocos += _blocos_visao_diaria(data_inicio, data_fim)
        if resumo_ant["invest"] > 0:
            blocos += _blocos_comparativo(resumo, resumo_ant, label_ant)
        blocos += _blocos_tendencia_mensal(data_inicio)

    else:
        # Semanal
        blocos += _blocos_resumo(resumo, periodo_str)
        blocos += _blocos_funil(resumo)
        blocos += _blocos_metricas(resumo)
        blocos += _blocos_campanhas(campanhas)
        blocos += _blocos_visao_diaria(data_inicio, data_fim)
        if resumo_ant["invest"] > 0:
            blocos += _blocos_comparativo(resumo, resumo_ant, label_ant)

    logger.info("[POD] %d campanhas · Invest.: %s · ROAS: %s",
                resumo["num_campanhas"], _fmt_brl(resumo["invest"]), _fmt_x(resumo["roas"]))

    if not publicar:
        return {
            "titulo": titulo,
            "periodo": periodo_str,
            "resumo": resumo,
            "blocos": blocos,
            "erro": None,
        }

    # ── Publica no Notion ─────────────────────────────────────────────────
    try:
        from notion.historico import criar_relatorio_no_historico
        resultado = criar_relatorio_no_historico(
            titulo=titulo,
            blocos=blocos,
            periodo_str=periodo_str,
            tipo=tipo,
            cliente="pod_kombucha",
        )
        return {
            "titulo":  titulo,
            "periodo": periodo_str,
            "resumo":  resumo,
            "page_id": resultado.get("page_id"),
            "url":     resultado.get("url"),
            "erro":    None,
        }
    except Exception as e:
        logger.error("[POD] Erro ao publicar no Notion: %s", e)
        return {"titulo": titulo, "periodo": periodo_str, "resumo": resumo, "erro": str(e)}


def gerar_relatorio_semanal_pod(
    data_inicio: date = None,
    data_fim: date = None,
    publicar: bool = True,
) -> dict:
    return gerar_relatorio_pod(data_inicio, data_fim, tipo="semanal", publicar=publicar)


def gerar_relatorio_mensal_pod(
    data_inicio: date = None,
    data_fim: date = None,
    publicar: bool = True,
) -> dict:
    return gerar_relatorio_pod(data_inicio, data_fim, tipo="mensal", publicar=publicar)
