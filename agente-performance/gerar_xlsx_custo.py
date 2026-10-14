"""
Gera xlsx com curvas de custo × nº de clientes:
- Agente de Performance (BQ + Claude Haiku)
- Agente de Transcrição (BQ + Claude Haiku)
Baseado em consumo real medido via dry run BigQuery em 2026-10-14.
"""
from dotenv import load_dotenv
load_dotenv()

import openpyxl
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── Constantes globais ───────────────────────────────────────────────────────
BQ_USD_PER_TB   = 6.25
FREE_TIER_TB    = 1.0
USD_TO_BRL      = 5.70
INFRA_FIXA_BRL  = 280.0   # servidor EC2 + Redis + domínio (compartilhado)

# Claude Haiku 4.5
CLAUDE_INPUT_PRICE  = 0.80 / 1_000_000   # US$/token
CLAUDE_OUTPUT_PRICE = 4.00 / 1_000_000

# ── Agente de Performance ────────────────────────────────────────────────────
# Baseline medido: 3 clientes, ads_diario = 6.8222 MB
PERF_MB_BASE         = 6.8222      # tamanho tabela com 3 clientes
PERF_CLI_BASE        = 3
PERF_CONSULTAS_MES   = 450         # 15/dia × 30
PERF_VIEWS_POR_CONS  = 2.5
PERF_SYNCS_DIA       = 5
# Média MB por view (excluindo full-scans, que são a tabela inteira)
PERF_MB_VIEWS_TOTAL  = 21.80       # soma dry run de 9 views com 3 clientes
PERF_N_VIEWS         = 9
PERF_TOKENS_IN       = 4000
PERF_TOKENS_OUT      = 500

# ── Agente de Transcrição ────────────────────────────────────────────────────
# Medido: analise_reunioes=0.0184MB, tickets_alerta=0.0093MB, metricas_sla=0.0629MB
# Total tabelas transcrição: 0.0906 MB com ~1 cliente/contexto (~5 reuniões/mês)
TRANS_MB_BASE        = 0.0906      # tamanho tabelas com 1 cliente-contexto
TRANS_CLI_BASE       = 1
# Consultas BQ do agente: v_analise_reunioes + v_tickets_alerta + v_alertas_campanha
TRANS_MB_VIEWS_TOTAL = 0.0184 + 0.0093 + 1.9650   # 2.0 MB por execução completa
TRANS_N_VIEWS        = 3
TRANS_CONSULTAS_MES  = 90          # ~3/dia (alertas + revisões) × 30
TRANS_VIEWS_POR_CONS = 2.0
TRANS_SYNCS_MES      = 30          # 1 sync/dia (upload de transcrições)
# Transcrição: ~1 reunião/semana por cliente = ~4/mês
# Texto médio: 1.241 chars resumo → ~2.000 tokens input + 1.024 output
TRANS_REUNIOES_MES   = 4
TRANS_TOKENS_IN      = 2000 + 800  # texto reunião + prompt sistema
TRANS_TOKENS_OUT     = 1024

def calcular_performance(n: int) -> dict:
    fator = n / PERF_CLI_BASE
    mb_tabela = PERF_MB_BASE * fator

    # Scan proporcional por view
    mb_por_view_med = (PERF_MB_VIEWS_TOTAL / PERF_N_VIEWS) * fator
    gb_consultas = (PERF_CONSULTAS_MES * fator * PERF_VIEWS_POR_CONS * mb_por_view_med) / 1024
    gb_syncs     = (PERF_SYNCS_DIA * 30 * n * mb_tabela) / 1024
    gb_total     = gb_consultas + gb_syncs
    tb_total     = gb_total / 1024
    tb_pago      = max(0, tb_total - FREE_TIER_TB)

    custo_bq_usd     = tb_pago * BQ_USD_PER_TB
    custo_bq_brl     = custo_bq_usd * USD_TO_BRL

    consultas_tot    = PERF_CONSULTAS_MES * fator
    custo_claude_usd = consultas_tot * (PERF_TOKENS_IN * CLAUDE_INPUT_PRICE + PERF_TOKENS_OUT * CLAUDE_OUTPUT_PRICE)
    custo_claude_brl = custo_claude_usd * USD_TO_BRL

    total = custo_bq_brl + custo_claude_brl + INFRA_FIXA_BRL
    return {
        "n": n,
        "gb": round(gb_total, 2),
        "custo_bq_brl":     round(custo_bq_brl, 2),
        "custo_claude_brl": round(custo_claude_brl, 2),
        "custo_total_brl":  round(total, 2),
        "custo_por_cli":    round(total / n, 2),
    }

def calcular_transcricao(n: int) -> dict:
    fator = n / TRANS_CLI_BASE
    mb_tabela = TRANS_MB_BASE * fator

    mb_por_view_med = (TRANS_MB_VIEWS_TOTAL / TRANS_N_VIEWS) * fator
    gb_consultas = (TRANS_CONSULTAS_MES * fator * TRANS_VIEWS_POR_CONS * mb_por_view_med) / 1024
    gb_syncs     = (TRANS_SYNCS_MES * n * mb_tabela) / 1024
    gb_total     = gb_consultas + gb_syncs
    tb_total     = gb_total / 1024
    tb_pago      = max(0, tb_total - FREE_TIER_TB)

    custo_bq_usd     = tb_pago * BQ_USD_PER_TB
    custo_bq_brl     = custo_bq_usd * USD_TO_BRL

    # Claude: análise de reuniões (n clientes × 4 reuniões/mês)
    analises_mes     = n * TRANS_REUNIOES_MES
    custo_claude_usd = analises_mes * (TRANS_TOKENS_IN * CLAUDE_INPUT_PRICE + TRANS_TOKENS_OUT * CLAUDE_OUTPUT_PRICE)
    custo_claude_brl = custo_claude_usd * USD_TO_BRL

    total = custo_bq_brl + custo_claude_brl + INFRA_FIXA_BRL
    return {
        "n": n,
        "gb": round(gb_total, 4),
        "custo_bq_brl":     round(custo_bq_brl, 2),
        "custo_claude_brl": round(custo_claude_brl, 2),
        "custo_total_brl":  round(total, 2),
        "custo_por_cli":    round(total / n, 2),
    }

N_MAX = 20
perf_data  = [calcular_performance(n)  for n in range(1, N_MAX + 1)]
trans_data = [calcular_transcricao(n)  for n in range(1, N_MAX + 1)]

# ── Workbook ─────────────────────────────────────────────────────────────────
wb = openpyxl.Workbook()

HDR_FILL_PERF  = PatternFill("solid", fgColor="1E3A5F")
HDR_FILL_TRANS = PatternFill("solid", fgColor="2D6A4F")
HDR_FONT       = Font(bold=True, color="FFFFFF", size=10)
ALT_FILL_PERF  = PatternFill("solid", fgColor="EBF2FB")
ALT_FILL_TRANS = PatternFill("solid", fgColor="E8F5E9")
BDR = Border(
    left=Side(style="thin", color="CCCCCC"),
    right=Side(style="thin", color="CCCCCC"),
    top=Side(style="thin", color="CCCCCC"),
    bottom=Side(style="thin", color="CCCCCC"),
)

def escrever_tabela(ws, titulo, dados, hdr_fill, alt_fill):
    headers = [
        ("Clientes", 10), ("GB/mês BQ", 12), ("Custo BQ (R$)", 14),
        ("Custo Claude (R$)", 16), ("Custo Total (R$)", 16), ("Custo/Cliente (R$)", 18),
    ]
    # Título
    ws.merge_cells(f"A1:{get_column_letter(len(headers))}1")
    c = ws["A1"]
    c.value = titulo
    c.font = Font(bold=True, color="FFFFFF", size=12)
    c.fill = hdr_fill
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 24

    for col, (h, w) in enumerate(headers, 1):
        c = ws.cell(row=2, column=col, value=h)
        c.font = HDR_FONT
        c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = BDR
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.row_dimensions[2].height = 22

    for i, d in enumerate(dados):
        row = i + 3
        fill = alt_fill if i % 2 == 0 else PatternFill("solid", fgColor="FFFFFF")
        vals = [d["n"], d["gb"], d["custo_bq_brl"], d["custo_claude_brl"],
                d["custo_total_brl"], d["custo_por_cli"]]
        for col, v in enumerate(vals, 1):
            c = ws.cell(row=row, column=col, value=v)
            c.fill = fill
            c.border = BDR
            c.alignment = Alignment(horizontal="center")
            if col >= 3:
                c.number_format = 'R$ #,##0.00'
    ws.freeze_panes = "A3"

# ── Aba Performance ──────────────────────────────────────────────────────────
ws_perf = wb.active
ws_perf.title = "Performance"
escrever_tabela(ws_perf, "Agente de Performance — Custo × Escala", perf_data, HDR_FILL_PERF, ALT_FILL_PERF)

notas_perf = [
    "Baseline medido: 3 clientes, ads_diario = 6,82 MB (dry run BQ 2026-10-14)",
    "BQ free tier: 1 TB/mês. Custo só sobre o excedente (US$ 6,25/TB).",
    "Claude Haiku 4.5: US$ 0,80/1M input + US$ 4,00/1M output. ~450 consultas/mês base.",
    "Infra fixa: R$ 280/mês (EC2 + Redis + domínio). Câmbio: US$ 1 = R$ 5,70.",
]
for i, n in enumerate(notas_perf):
    r = 25 + i
    ws_perf.merge_cells(f"A{r}:F{r}")
    c = ws_perf.cell(row=r, column=1, value=("Metodologia:" if i == 0 else "") + ("  " if i > 0 else " ") + n)
    c.font = Font(size=9, color="555555", bold=(i == 0))

# ── Aba Transcrição ──────────────────────────────────────────────────────────
ws_trans = wb.create_sheet("Transcrição")
escrever_tabela(ws_trans, "Agente de Transcrição — Custo × Escala", trans_data, HDR_FILL_TRANS, ALT_FILL_TRANS)

notas_trans = [
    "Baseline medido: tabelas BQ transcrição = 0,09 MB (analise_reunioes + tickets_alerta + metricas_sla).",
    "Claude Haiku 4.5: ~4 reuniões/cliente/mês, ~2.800 tokens input + 1.024 output por análise.",
    "BQ free tier: 1 TB/mês. Custo BQ praticamente zero até muitos clientes (tabelas muito pequenas).",
    "Infra fixa: R$ 280/mês compartilhada. Câmbio: US$ 1 = R$ 5,70.",
]
for i, n in enumerate(notas_trans):
    r = 25 + i
    ws_trans.merge_cells(f"A{r}:F{r}")
    c = ws_trans.cell(row=r, column=1, value=("Metodologia:" if i == 0 else "") + ("  " if i > 0 else " ") + n)
    c.font = Font(size=9, color="555555", bold=(i == 0))

# ── Aba Comparativo + Gráfico ────────────────────────────────────────────────
wc = wb.create_sheet("Comparativo — Gráfico")

wc["A1"] = "Nº Clientes"
wc["B1"] = "Performance — Total (R$)"
wc["C1"] = "Transcrição — Total (R$)"
wc["D1"] = "Performance — Por Cliente (R$)"
wc["E1"] = "Transcrição — Por Cliente (R$)"

for i in range(N_MAX):
    r = i + 2
    wc.cell(r, 1, perf_data[i]["n"])
    wc.cell(r, 2, perf_data[i]["custo_total_brl"])
    wc.cell(r, 3, trans_data[i]["custo_total_brl"])
    wc.cell(r, 4, perf_data[i]["custo_por_cli"])
    wc.cell(r, 5, trans_data[i]["custo_por_cli"])

wc.column_dimensions["A"].width = 14
for col in ["B","C","D","E"]:
    wc.column_dimensions[col].width = 26

# Gráfico 1: Custo Total
chart1 = LineChart()
chart1.title = "Custo Total Mensal × Nº de Clientes"
chart1.style = 10
chart1.y_axis.title = "Custo Mensal (R$)"
chart1.x_axis.title = "Nº de Clientes"
chart1.height = 14
chart1.width  = 24
cats = Reference(wc, min_col=1, min_row=2, max_row=N_MAX + 1)
data1 = Reference(wc, min_col=2, max_col=3, min_row=1, max_row=N_MAX + 1)
chart1.add_data(data1, titles_from_data=True)
chart1.set_categories(cats)
chart1.series[0].graphicalProperties.line.solidFill = "1E3A5F"
chart1.series[0].graphicalProperties.line.width = 20000
chart1.series[0].marker.symbol = "circle"
chart1.series[0].marker.size = 5
chart1.series[1].graphicalProperties.line.solidFill = "2D6A4F"
chart1.series[1].graphicalProperties.line.width = 20000
chart1.series[1].marker.symbol = "diamond"
chart1.series[1].marker.size = 5
wc.add_chart(chart1, "G2")

# Gráfico 2: Custo por Cliente
chart2 = LineChart()
chart2.title = "Custo por Cliente × Escala (diluição)"
chart2.style = 10
chart2.y_axis.title = "Custo por Cliente (R$/mês)"
chart2.x_axis.title = "Nº de Clientes"
chart2.height = 14
chart2.width  = 24
data2 = Reference(wc, min_col=4, max_col=5, min_row=1, max_row=N_MAX + 1)
chart2.add_data(data2, titles_from_data=True)
chart2.set_categories(cats)
chart2.series[0].graphicalProperties.line.solidFill = "1E3A5F"
chart2.series[0].graphicalProperties.line.width = 20000
chart2.series[0].marker.symbol = "circle"
chart2.series[0].marker.size = 5
chart2.series[1].graphicalProperties.line.solidFill = "2D6A4F"
chart2.series[1].graphicalProperties.line.width = 20000
chart2.series[1].marker.symbol = "diamond"
chart2.series[1].marker.size = 5
wc.add_chart(chart2, "G20")

# ── Salvar ───────────────────────────────────────────────────────────────────
out = "agente_performance_custo_escala_bq.xlsx"
wb.save(out)
print(f"Salvo: {out}")

print("\nPerformance:")
for n in [1, 3, 5, 10, 20]:
    d = calcular_performance(n)
    print(f"  {n:2d} cli → Total: R$ {d['custo_total_brl']:,.0f} | Por cli: R$ {d['custo_por_cli']:,.0f} | BQ: {d['gb']:.1f} GB")

print("\nTranscrição:")
for n in [1, 3, 5, 10, 20]:
    d = calcular_transcricao(n)
    print(f"  {n:2d} cli → Total: R$ {d['custo_total_brl']:,.0f} | Por cli: R$ {d['custo_por_cli']:,.0f} | BQ: {d['gb']:.4f} GB")
