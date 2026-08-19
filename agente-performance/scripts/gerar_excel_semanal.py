#!/usr/bin/env python3
"""
Gerador de relatórios Excel semanais — Agente de Performance Guará
Gera um .xlsx por cliente, com abas por semana/plataforma.
"""
import os
import sys
from pathlib import Path
from datetime import date, timedelta
import sqlite3

# Adiciona o diretório raiz ao path
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import openpyxl
from openpyxl.styles import (
    PatternFill, Font, Alignment, Border, Side, numbers
)
from openpyxl.utils import get_column_letter

DB_PATH = BASE_DIR / "data" / "campanhas.db"
OUT_DIR = BASE_DIR / "reports"
OUT_DIR.mkdir(exist_ok=True)

# ── Paleta de cores ───────────────────────────────────────────────────────────
AZUL_ESCURO  = "1F3864"
AZUL_MEDIO   = "2E75B6"
AZUL_CLARO   = "D9E1F2"
VERDE        = "C6EFCE"
AMARELO      = "FFEB9C"
VERMELHO     = "FFC7CE"
BRANCO       = "FFFFFF"
CINZA_LINHA  = "F2F2F2"

def fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)

def font(bold=False, color=BRANCO, size=11):
    return Font(bold=bold, color=color, size=size)

def border():
    thin = Side(style="thin", color="BFBFBF")
    return Border(left=thin, right=thin, top=thin, bottom=thin)

def fmt_brl(ws, row, col, value):
    cell = ws.cell(row=row, column=col, value=value)
    cell.number_format = u'R$ #,##0.00'
    cell.border = border()
    return cell

def fmt_pct(ws, row, col, value):
    cell = ws.cell(row=row, column=col, value=value / 100 if value else 0)
    cell.number_format = '0.00%'
    cell.border = border()
    return cell

def fmt_num(ws, row, col, value):
    cell = ws.cell(row=row, column=col, value=value or 0)
    cell.number_format = '#,##0'
    cell.border = border()
    return cell

def fmt_x(ws, row, col, value):
    cell = ws.cell(row=row, column=col, value=value or 0)
    cell.number_format = '0.00"x"'
    cell.border = border()
    # cor por ROAS
    if value:
        if value >= 4:
            cell.fill = fill(VERDE)
        elif value >= 2:
            cell.fill = fill(AMARELO)
        else:
            cell.fill = fill(VERMELHO)
    return cell

def fmt_delta(ws, row, col, atual, anterior):
    if anterior and anterior != 0:
        delta = (atual - anterior) / abs(anterior) * 100
    else:
        delta = None
    cell = ws.cell(row=row, column=col, value=delta/100 if delta is not None else None)
    cell.number_format = '+0.0%;-0.0%;0.0%'
    cell.border = border()
    if delta is not None:
        cell.fill = fill(VERDE) if delta >= 0 else fill(VERMELHO)
    return cell

def auto_width(ws):
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                val = str(cell.value or "")
                max_len = max(max_len, len(val))
            except:
                pass
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 40)

def header_row(ws, row, cols, bg=AZUL_MEDIO):
    for i, col_name in enumerate(cols, 1):
        c = ws.cell(row=row, column=i, value=col_name)
        c.fill = fill(bg)
        c.font = font(bold=True, color=BRANCO, size=10)
        c.alignment = Alignment(horizontal="center")
        c.border = border()

def section_title(ws, row, col, text, span=8, bg=AZUL_MEDIO):
    c = ws.cell(row=row, column=col, value=text)
    c.fill = fill(bg)
    c.font = font(bold=True, color=BRANCO, size=11)
    if span > 1:
        ws.merge_cells(start_row=row, start_column=col,
                       end_row=row, end_column=col + span - 1)
    return row + 1

def db_query(sql, params=()):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def semanas_do_periodo(data_inicio: date, data_fim: date):
    """Retorna lista de (seg, dom) para cada semana no período."""
    semanas = []
    d = data_inicio
    # Vai para a segunda-feira da semana de data_inicio
    d = d - timedelta(days=d.weekday())
    while d <= data_fim:
        seg = d
        dom = d + timedelta(days=6)
        semanas.append((seg, dom))
        d += timedelta(days=7)
    return semanas

def resumo_semana_ads(cliente_key, plataforma, seg, dom):
    rows = db_query("""
        SELECT
            COUNT(DISTINCT campanha) AS campanhas,
            ROUND(SUM(impressoes),0) AS impressoes,
            ROUND(SUM(cliques),0) AS cliques,
            ROUND(SUM(alcance),0) AS alcance,
            ROUND(SUM(investimento),2) AS investimento,
            ROUND(SUM(conversoes),0) AS conversoes,
            ROUND(SUM(receita),2) AS receita,
            ROUND(SUM(receita)/NULLIF(SUM(investimento),0),2) AS roas,
            ROUND(SUM(cliques)/NULLIF(SUM(impressoes),0)*100,4) AS ctr,
            ROUND(SUM(investimento)/NULLIF(SUM(cliques),0),2) AS cpc,
            ROUND(SUM(investimento)/NULLIF(SUM(impressoes),0)*1000,2) AS cpm
        FROM v_performance
        WHERE data BETWEEN ? AND ? AND cliente_key = ? AND plataforma = ?
    """, (str(seg), str(dom), cliente_key, plataforma))
    return rows[0] if rows else {}

def campanhas_semana(cliente_key, plataforma, seg, dom):
    return db_query("""
        SELECT
            campanha,
            ROUND(SUM(investimento),2) AS investimento,
            ROUND(SUM(receita),2) AS receita,
            ROUND(SUM(receita)/NULLIF(SUM(investimento),0),2) AS roas,
            ROUND(SUM(conversoes),0) AS conversoes,
            ROUND(SUM(cliques),0) AS cliques,
            ROUND(SUM(impressoes),0) AS impressoes,
            ROUND(SUM(cliques)/NULLIF(SUM(impressoes),0)*100,4) AS ctr
        FROM v_performance
        WHERE data BETWEEN ? AND ? AND cliente_key = ? AND plataforma = ?
        GROUP BY campanha
        ORDER BY investimento DESC
    """, (str(seg), str(dom), cliente_key, plataforma))

def shopify_semana(seg, dom):
    rows = db_query("""
        SELECT
            SUM(pedidos) AS pedidos,
            ROUND(SUM(receita),2) AS receita,
            SUM(novos_clientes) AS novos_clientes,
            SUM(recompradores) AS recompradores,
            ROUND(SUM(receita)/NULLIF(SUM(pedidos),0),2) AS ticket_medio,
            ROUND(SUM(total_desconto),2) AS total_desconto,
            SUM(pedidos_acima_300) AS pedidos_acima_300
        FROM shopify_receita_diaria
        WHERE data BETWEEN ? AND ?
    """, (str(seg), str(dom)))
    return rows[0] if rows else {}

def escrever_aba_ads(wb, tab_name, cliente_key, plataforma, semanas_com_dados):
    ws = wb.create_sheet(title=tab_name[:31])
    ws.freeze_panes = "A3"

    plat_label = "Meta Ads" if plataforma == "meta_ads" else "Google Ads"
    cliente_label = "Source OF" if cliente_key == "source_of" else "POD Kombucha"

    # Título principal
    titulo = f"{cliente_label} · {plat_label} — Análise Semanal"
    c = ws.cell(row=1, column=1, value=titulo)
    c.fill = fill(AZUL_ESCURO)
    c.font = Font(bold=True, color=BRANCO, size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=12)
    ws.row_dimensions[1].height = 22

    cur_row = 3
    ant = {}

    for seg, dom, resumo in semanas_com_dados:
        periodo = f"{seg.strftime('%d/%m')} – {dom.strftime('%d/%m/%Y')}"
        camps   = campanhas_semana(cliente_key, plataforma, seg, dom)

        # Cabeçalho da semana
        c = ws.cell(row=cur_row, column=1, value=f"📅 Semana: {periodo}")
        c.fill = fill(AZUL_ESCURO)
        c.font = Font(bold=True, color=BRANCO, size=12)
        ws.merge_cells(start_row=cur_row, start_column=1, end_row=cur_row, end_column=12)
        cur_row += 1

        # Resumo
        cur_row = section_title(ws, cur_row, 1, "Resumo da Semana", span=6, bg=AZUL_MEDIO)
        header_row(ws, cur_row, ["Métrica", "Valor", "Vs Semana Anterior", "", "", ""], bg=AZUL_CLARO)
        ws.cell(row=cur_row, column=1).font = Font(bold=True, color="000000", size=10)
        ws.cell(row=cur_row, column=2).font = Font(bold=True, color="000000", size=10)
        ws.cell(row=cur_row, column=3).font = Font(bold=True, color="000000", size=10)
        cur_row += 1

        metricas = [
            ("💰 Investimento",  resumo.get("investimento"), ant.get("investimento"), "brl"),
            ("📈 Receita",        resumo.get("receita"),      ant.get("receita"),      "brl"),
            ("🏆 ROAS",           resumo.get("roas"),         ant.get("roas"),         "x"),
            ("✅ Conversões",     resumo.get("conversoes"),   ant.get("conversoes"),   "num"),
            ("🖥️ Impressões",   resumo.get("impressoes"),  ant.get("impressoes"),   "num"),
            ("🔗 Cliques",        resumo.get("cliques"),      ant.get("cliques"),      "num"),
            ("🎯 CTR",            resumo.get("ctr"),          ant.get("ctr"),          "pct"),
            ("💸 CPC",            resumo.get("cpc"),          ant.get("cpc"),          "brl"),
            ("📣 CPM",            resumo.get("cpm"),          ant.get("cpm"),          "brl"),
            ("👥 Alcance",        resumo.get("alcance"),      ant.get("alcance"),      "num"),
        ]
        for label, val, ant_val, tipo in metricas:
            ws.cell(row=cur_row, column=1, value=label).border = border()
            if tipo == "brl":
                fmt_brl(ws, cur_row, 2, val)
            elif tipo == "x":
                fmt_x(ws, cur_row, 2, val)
            elif tipo == "pct":
                fmt_pct(ws, cur_row, 2, val)
            else:
                fmt_num(ws, cur_row, 2, val)
            fmt_delta(ws, cur_row, 3, val or 0, ant_val)
            ws.cell(row=cur_row, column=1).fill = fill(CINZA_LINHA) if cur_row % 2 == 0 else PatternFill()
            cur_row += 1

        cur_row += 1

        # Campanhas
        if camps:
            cur_row = section_title(ws, cur_row, 1, f"Desempenho por Campanha ({len(camps)} campanhas)", span=8, bg=AZUL_MEDIO)
            cols_camp = ["Campanha", "Investimento", "Receita", "ROAS", "Conversões", "Cliques", "Impressões", "CTR"]
            header_row(ws, cur_row, cols_camp, bg=AZUL_CLARO)
            for c_idx, _ in enumerate(cols_camp, 1):
                ws.cell(row=cur_row, column=c_idx).font = Font(bold=True, color="000000", size=10)
            cur_row += 1

            for camp in camps:
                ws.cell(row=cur_row, column=1, value=camp.get("campanha", "")).border = border()
                fmt_brl(ws, cur_row, 2, camp.get("investimento"))
                fmt_brl(ws, cur_row, 3, camp.get("receita"))
                fmt_x(ws, cur_row, 4, camp.get("roas"))
                fmt_num(ws, cur_row, 5, camp.get("conversoes"))
                fmt_num(ws, cur_row, 6, camp.get("cliques"))
                fmt_num(ws, cur_row, 7, camp.get("impressoes"))
                fmt_pct(ws, cur_row, 8, camp.get("ctr"))
                if cur_row % 2 == 0:
                    for col in range(1, 9):
                        cell = ws.cell(row=cur_row, column=col)
                        if not cell.fill or cell.fill.patternType is None or cell.fill.fgColor.rgb in (VERDE, AMARELO, VERMELHO):
                            pass
                        else:
                            cell.fill = fill(CINZA_LINHA)
                cur_row += 1

        cur_row += 2
        ant = resumo

    auto_width(ws)
    return ws

def escrever_aba_shopify(wb, semanas_com_dados):
    ws = wb.create_sheet(title="Shopify")
    ws.freeze_panes = "A3"

    c = ws.cell(row=1, column=1, value="Source OF · Shopify — Análise Semanal")
    c.fill = fill(AZUL_ESCURO)
    c.font = Font(bold=True, color=BRANCO, size=14)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=8)
    ws.row_dimensions[1].height = 22

    cur_row = 3
    ant = {}

    for seg, dom, dados in semanas_com_dados:
        if not dados or not dados.get("pedidos"):
            continue
        periodo = f"{seg.strftime('%d/%m')} – {dom.strftime('%d/%m/%Y')}"

        c = ws.cell(row=cur_row, column=1, value=f"📅 Semana: {periodo}")
        c.fill = fill(AZUL_ESCURO)
        c.font = Font(bold=True, color=BRANCO, size=12)
        ws.merge_cells(start_row=cur_row, start_column=1, end_row=cur_row, end_column=8)
        cur_row += 1

        cur_row = section_title(ws, cur_row, 1, "Resumo Shopify", span=4, bg=AZUL_MEDIO)
        header_row(ws, cur_row, ["Métrica", "Valor", "Vs Semana Anterior", ""], bg=AZUL_CLARO)
        for ci in range(1, 5):
            ws.cell(row=cur_row, column=ci).font = Font(bold=True, color="000000", size=10)
        cur_row += 1

        metricas = [
            ("📦 Pedidos",            dados.get("pedidos"),           ant.get("pedidos"),       "num"),
            ("💰 Receita",             dados.get("receita"),           ant.get("receita"),        "brl"),
            ("🏷️ Ticket Médio",      dados.get("ticket_medio"),      ant.get("ticket_medio"),   "brl"),
            ("🆕 Novos Clientes",      dados.get("novos_clientes"),    ant.get("novos_clientes"), "num"),
            ("🔁 Recompradores",       dados.get("recompradores"),     ant.get("recompradores"),  "num"),
            ("🎟️ Total Descontos",   dados.get("total_desconto"),    ant.get("total_desconto"), "brl"),
            ("🏅 Pedidos > R$300",     dados.get("pedidos_acima_300"), ant.get("pedidos_acima_300"), "num"),
        ]
        for label, val, ant_val, tipo in metricas:
            ws.cell(row=cur_row, column=1, value=label).border = border()
            if tipo == "brl":
                fmt_brl(ws, cur_row, 2, val)
            else:
                fmt_num(ws, cur_row, 2, val)
            fmt_delta(ws, cur_row, 3, val or 0, ant_val)
            cur_row += 1

        cur_row += 2
        ant = dados

    auto_width(ws)
    return ws

def escrever_aba_consolidado(wb, cliente_key, semanas, plataformas_disponiveis):
    ws = wb.create_sheet(title="Consolidado")
    ws.freeze_panes = "B3"

    cliente_label = "Source OF" if cliente_key == "source_of" else "POD Kombucha"
    c = ws.cell(row=1, column=1, value=f"{cliente_label} — Consolidado Semanal por Plataforma")
    c.fill = fill(AZUL_ESCURO)
    c.font = Font(bold=True, color=BRANCO, size=14)
    n_cols = 3 + len(plataformas_disponiveis) * 3
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
    ws.row_dimensions[1].height = 22

    # Header
    cur_row = 3
    ws.cell(row=cur_row, column=1, value="Semana").fill = fill(AZUL_ESCURO)
    ws.cell(row=cur_row, column=1).font = font(bold=True, size=10)

    col = 2
    plat_labels = {"meta_ads": "Meta Ads", "google_ads": "Google Ads", "shopify": "Shopify"}
    for plat in plataformas_disponiveis:
        label = plat_labels.get(plat, plat)
        for h in ["Invest.", "Receita", "ROAS"]:
            c = ws.cell(row=cur_row, column=col, value=f"{label} — {h}")
            c.fill = fill(AZUL_MEDIO)
            c.font = font(bold=True, size=9)
            c.alignment = Alignment(horizontal="center", wrap_text=True)
            c.border = border()
            col += 1

    cur_row += 1
    for seg, dom in semanas:
        periodo = f"{seg.strftime('%d/%m')} – {dom.strftime('%d/%m')}"
        ws.cell(row=cur_row, column=1, value=periodo).border = border()
        col = 2
        for plat in plataformas_disponiveis:
            if plat == "shopify":
                dados = shopify_semana(seg, dom)
                inv  = None
                rec  = dados.get("receita")
                roas = None
            else:
                dados = resumo_semana_ads(cliente_key, plat, seg, dom)
                inv  = dados.get("investimento")
                rec  = dados.get("receita")
                roas = dados.get("roas")

            fmt_brl(ws, cur_row, col, inv)
            fmt_brl(ws, cur_row, col+1, rec)
            fmt_x(ws, cur_row, col+2, roas)
            col += 3

        if cur_row % 2 == 0:
            ws.cell(row=cur_row, column=1).fill = fill(CINZA_LINHA)
        cur_row += 1

    auto_width(ws)
    return ws

def gerar_cliente(cliente_key):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Range de datas disponíveis
    plataformas = []
    if cliente_key == "source_of":
        plataformas = ["meta_ads", "google_ads"]
    else:
        plataformas = ["meta_ads", "google_ads"]

    # Pega range geral
    rows = conn.execute("""
        SELECT MIN(data) as ini, MAX(data) as fim
        FROM v_performance
        WHERE cliente_key = ?
    """, (cliente_key,)).fetchone()
    conn.close()

    if not rows or not rows["ini"]:
        print(f"[{cliente_key}] Sem dados. Pulando.")
        return

    data_ini = date.fromisoformat(rows["ini"])
    data_fim = date.fromisoformat(rows["fim"])

    # Limitar a ~3 meses para não gerar arquivo enorme
    tres_meses = data_fim - timedelta(days=90)
    data_ini = max(data_ini, tres_meses)

    semanas = semanas_do_periodo(data_ini, data_fim)
    print(f"[{cliente_key}] {len(semanas)} semanas de {data_ini} a {data_fim}")

    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # Remove aba padrão

    plats_com_dados = []
    for plat in plataformas:
        semanas_dados = []
        for seg, dom in semanas:
            r = resumo_semana_ads(cliente_key, plat, seg, dom)
            if r and r.get("investimento"):
                semanas_dados.append((seg, dom, r))

        if semanas_dados:
            plat_label = "Meta_Ads" if plat == "meta_ads" else "Google_Ads"
            escrever_aba_ads(wb, plat_label, cliente_key, plat, semanas_dados)
            plats_com_dados.append(plat)
            print(f"  [{cliente_key}] {plat}: {len(semanas_dados)} semanas com dados")

    # Shopify (só source_of)
    if cliente_key == "source_of":
        shop_semanas = []
        for seg, dom in semanas:
            d = shopify_semana(seg, dom)
            if d and d.get("pedidos"):
                shop_semanas.append((seg, dom, d))
        if shop_semanas:
            escrever_aba_shopify(wb, shop_semanas)
            plats_com_dados.append("shopify")
            print(f"  [{cliente_key}] shopify: {len(shop_semanas)} semanas com dados")

    # Consolidado
    if plats_com_dados:
        escrever_aba_consolidado(wb, cliente_key, semanas, plats_com_dados)

    out_path = OUT_DIR / f"{cliente_key}_relatorio_semanal.xlsx"
    wb.save(str(out_path))
    size_kb = out_path.stat().st_size / 1024
    print(f"  ✅ Salvo: {out_path} ({size_kb:.1f} KB)")
    return out_path

if __name__ == "__main__":
    print("Gerando relatórios Excel semanais...")
    for cliente in ["source_of", "pod_kombucha"]:
        gerar_cliente(cliente)
    print("Concluído.")
