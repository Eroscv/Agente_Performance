"""
reports/exporter.py

Gera relatórios exportáveis em Excel (.xlsx) e PDF.

Exporta:
  - gerar_excel(dados, titulo, colunas) → bytes
  - gerar_pdf(dados, titulo, colunas)   → bytes
  - gerar_relatorio(resultado, pergunta) → (bytes, filename, mimetype)
"""

import io
import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


# ── Excel ─────────────────────────────────────────────────────────────────────

def gerar_excel(dados: list[dict], titulo: str = "Relatório") -> bytes:
    """Gera arquivo Excel (.xlsx) a partir de uma lista de dicts."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = titulo[:31]  # limite do Excel

    if not dados:
        ws["A1"] = "Nenhum dado encontrado."
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    colunas = list(dados[0].keys())

    # Estilo header
    header_fill = PatternFill("solid", fgColor="1a1a2e")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )

    # Linha de título
    ws.merge_cells(f"A1:{get_column_letter(len(colunas))}1")
    cell_titulo = ws["A1"]
    cell_titulo.value     = titulo
    cell_titulo.font      = Font(bold=True, size=13, color="1a1a2e")
    cell_titulo.alignment = Alignment(horizontal="center")
    ws.row_dimensions[1].height = 22

    # Linha de data
    ws.merge_cells(f"A2:{get_column_letter(len(colunas))}2")
    ws["A2"] = f"Gerado em {date.today().strftime('%d/%m/%Y')} — Agente de Performance Guará"
    ws["A2"].font      = Font(italic=True, size=9, color="666666")
    ws["A2"].alignment = Alignment(horizontal="center")
    ws.row_dimensions[2].height = 14

    # Headers
    for col_idx, col_name in enumerate(colunas, 1):
        cell = ws.cell(row=4, column=col_idx)
        cell.value     = col_name.replace("_", " ").title()
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border    = thin_border

    ws.row_dimensions[4].height = 18

    # Dados
    alt_fill = PatternFill("solid", fgColor="f0f4ff")
    for row_idx, row in enumerate(dados, 5):
        for col_idx, col_name in enumerate(colunas, 1):
            val  = row.get(col_name)
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.value  = val
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="right" if isinstance(val, (int, float)) else "left")
            if row_idx % 2 == 0:
                cell.fill = alt_fill

    # Auto-ajusta largura das colunas
    for col_idx, col_name in enumerate(colunas, 1):
        col_letter = get_column_letter(col_idx)
        max_len = max(
            len(str(col_name)),
            max((len(str(row.get(col_name, "") or "")) for row in dados), default=0),
        )
        ws.column_dimensions[col_letter].width = min(max_len + 4, 40)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── PDF ───────────────────────────────────────────────────────────────────────

def gerar_pdf(dados: list[dict], titulo: str = "Relatório") -> bytes:
    """Gera arquivo PDF a partir de uma lista de dicts."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.enums import TA_CENTER

    buf = io.BytesIO()

    # Usa landscape se muitas colunas
    n_cols   = len(dados[0].keys()) if dados else 1
    pagesize = landscape(A4) if n_cols > 6 else A4

    doc = SimpleDocTemplate(
        buf, pagesize=pagesize,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
    )

    styles  = getSampleStyleSheet()
    story   = []
    cor_pri = colors.HexColor("#1a1a2e")
    cor_alt = colors.HexColor("#f0f4ff")

    # Título
    style_titulo = ParagraphStyle(
        "titulo", parent=styles["Title"],
        textColor=cor_pri, fontSize=16, spaceAfter=4,
    )
    story.append(Paragraph(titulo, style_titulo))

    # Subtítulo
    story.append(Paragraph(
        f"Gerado em {date.today().strftime('%d/%m/%Y')} — Agente de Performance Guará",
        ParagraphStyle("sub", parent=styles["Normal"], textColor=colors.grey, fontSize=9),
    ))
    story.append(Spacer(1, 0.4*cm))

    if not dados:
        story.append(Paragraph("Nenhum dado encontrado.", styles["Normal"]))
        doc.build(story)
        return buf.getvalue()

    colunas    = list(dados[0].keys())
    col_labels = [c.replace("_", " ").title() for c in colunas]

    # Cabeçalho + linhas
    table_data = [col_labels]
    for row in dados:
        line = []
        for col in colunas:
            val = row.get(col)
            if val is None:
                val = "—"
            elif isinstance(val, float):
                val = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            line.append(str(val))
        table_data.append(line)

    # Calcula largura disponível e divide igualmente
    page_w  = pagesize[0] - 3*cm
    col_w   = page_w / n_cols

    table = Table(table_data, colWidths=[col_w] * n_cols, repeatRows=1)
    table.setStyle(TableStyle([
        # Header
        ("BACKGROUND",  (0, 0), (-1, 0),  cor_pri),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0),  9),
        ("ALIGN",       (0, 0), (-1, 0),  "CENTER"),
        ("TOPPADDING",  (0, 0), (-1, 0),  6),
        ("BOTTOMPADDING",(0,0), (-1, 0),  6),
        # Dados
        ("FONTNAME",    (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",    (0, 1), (-1, -1), 8),
        ("ALIGN",       (0, 1), (-1, -1), "CENTER"),
        ("TOPPADDING",  (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING",(0,1), (-1, -1), 4),
        # Linhas alternadas
        *[("BACKGROUND", (0, i), (-1, i), cor_alt) for i in range(2, len(table_data), 2)],
        # Grid
        ("GRID",        (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("BOX",         (0, 0), (-1, -1), 0.8, cor_pri),
    ]))

    story.append(table)
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        f"Total: {len(dados)} registros",
        ParagraphStyle("rodape", parent=styles["Normal"], textColor=colors.grey, fontSize=8),
    ))

    doc.build(story)
    return buf.getvalue()


# ── Gerador principal ─────────────────────────────────────────────────────────

def gerar_relatorio(
    resultado: dict,
    pergunta:  str,
    formato:   str = "excel",  # "excel" | "pdf"
) -> tuple[bytes, str, str]:
    """
    Gera relatório a partir do resultado de uma consulta.

    Returns:
        (bytes, filename, mimetype)
    """
    dados  = resultado.get("dados", [])
    intent = resultado.get("intent", "relatorio")
    meta   = resultado.get("meta", {})

    # Normaliza dados para lista de dicts
    if isinstance(dados, dict):
        dados = [dados]
    if not isinstance(dados, list):
        dados = []

    # Monta título
    campanha = meta.get("campanha", "")
    d_ini    = meta.get("data_inicio", "")
    d_fim    = meta.get("data_fim", "")
    partes   = ["Agente de Performance Guará"]
    if campanha:
        partes.append(campanha)
    if d_ini and d_fim:
        partes.append(f"{d_ini} a {d_fim}")
    elif d_ini:
        partes.append(f"a partir de {d_ini}")

    titulo   = " — ".join(partes)
    hoje_str = date.today().strftime("%Y%m%d")

    if formato == "pdf":
        conteudo = gerar_pdf(dados, titulo)
        filename = f"guara_{intent}_{hoje_str}.pdf"
        mimetype = "application/pdf"
    else:
        conteudo = gerar_excel(dados, titulo)
        filename = f"guara_{intent}_{hoje_str}.xlsx"
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    logger.info("Relatório gerado: %s (%d bytes, %d linhas)", filename, len(conteudo), len(dados))
    return conteudo, filename, mimetype
