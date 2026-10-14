"""
Gera xlsx com curva de custo do Agente de Performance × nº de clientes,
baseado no consumo real do BigQuery.
"""
from dotenv import load_dotenv
load_dotenv()

import openpyxl
from openpyxl.chart import LineChart, Reference
from openpyxl.chart.series import DataPoint
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
from openpyxl.utils import get_column_letter
from datetime import date

# ── Parâmetros baseados em uso real ─────────────────────────────────────────

# Tamanho atual da tabela ads_diario: 6,82 MB com 3 clientes ativos
MB_POR_CLIENTE_BASE = 6.8222 / 3          # ~2,27 MB de scan/cliente (proporcional)

# Distribuição de views por tipo de consulta (dry run medido)
VIEWS_SCAN_MB = {
    "v_ads_diario":          6.8222,   # full scan ads_diario
    "v_performance_mensal":  1.0106,
    "v_mensal":              1.5616,
    "v_diario_90d":          1.0940,
    "v_performance_completa":0.9901,
    "v_alertas_campanha":    1.9650,
    "v_ads_roas_real":       6.8222,
    "v_top_campanhas_mes":   0.2802,
    "v_benchmark_mensal":    1.2586,
}
MB_TOTAL_VIEWS_3_CLI = sum(VIEWS_SCAN_MB.values())   # 21.8 MB / execução completa

# BQ on-demand: US$ 6,25/TB acima de 1TB free tier
BQ_USD_PER_TB  = 6.25
FREE_TIER_TB   = 1.0       # 1 TB/mês gratuito
USD_TO_BRL     = 5.70      # câmbio estimado

# Consultas/mês ao agente (estimativa conservadora)
CONSULTAS_MES  = 450       # ~15/dia × 30 dias
VIEWS_POR_CONS = 2.5       # média views por consulta

# Syncs diários: 2 contas Meta + 2 Google + Shopify = ~5 syncs/dia
SYNCS_DIA      = 5
MB_POR_SYNC    = 6.8222    # scan full ads_diario por sync
SYNCS_MES      = SYNCS_DIA * 30

# Claude API custo por consulta (estimativa)
CLAUDE_TOKENS_INPUT  = 4000   # tokens médios de input por consulta
CLAUDE_TOKENS_OUTPUT = 500
CLAUDE_PRECO_INPUT   = 0.80 / 1_000_000   # Haiku 4.5: US$0.80/1M input
CLAUDE_PRECO_OUTPUT  = 4.00 / 1_000_000   # Haiku 4.5: US$4.00/1M output
CLAUDE_CUSTO_CONS    = CLAUDE_TOKENS_INPUT * CLAUDE_PRECO_INPUT + CLAUDE_TOKENS_OUTPUT * CLAUDE_PRECO_OUTPUT

# Infra fixa mensal (servidor, Redis, domínio) — independente de clientes
INFRA_FIXA_BRL = 280.0     # ~US$50 × 5,70

def calcular_custo(n_clientes: int) -> dict:
    """Calcula custo mensal para n_clientes."""
    # BQ: tabela cresce proporcionalmente com clientes
    fator = n_clientes / 3.0   # 3 clientes = baseline atual
    mb_tabela = 6.8222 * fator

    # Scan por query escala com tamanho da tabela
    mb_por_view_escalonado = sum([
        mb_tabela,                        # v_ads_diario / v_ads_roas_real (full scan)
        mb_tabela * (1.0106 / 6.8222),   # v_performance_mensal
        mb_tabela * (1.5616 / 6.8222),   # v_mensal
        mb_tabela * (1.0940 / 6.8222),   # v_diario_90d
        mb_tabela * (0.9901 / 6.8222),   # v_performance_completa
        mb_tabela * (1.9650 / 6.8222),   # v_alertas_campanha
        mb_tabela,                        # v_ads_roas_real
        mb_tabela * (0.2802 / 6.8222),   # v_top_campanhas_mes
        mb_tabela * (1.2586 / 6.8222),   # v_benchmark_mensal
    ])

    # Total GB/mês: consultas + syncs
    gb_consultas = (CONSULTAS_MES * n_clientes/3 * VIEWS_POR_CONS * mb_por_view_escalonado / len(VIEWS_SCAN_MB)) / 1024
    gb_syncs     = (SYNCS_MES * n_clientes * mb_tabela) / 1024
    gb_total     = gb_consultas + gb_syncs
    tb_total     = gb_total / 1024

    # Custo BQ (acima do free tier)
    tb_pago      = max(0, tb_total - FREE_TIER_TB)
    custo_bq_usd = tb_pago * BQ_USD_PER_TB
    custo_bq_brl = custo_bq_usd * USD_TO_BRL

    # Custo Claude
    consultas_total  = CONSULTAS_MES * (n_clientes / 3)
    custo_claude_usd = consultas_total * CLAUDE_CUSTO_CONS
    custo_claude_brl = custo_claude_usd * USD_TO_BRL

    # Total variável + fixo
    custo_variavel_brl = custo_bq_brl + custo_claude_brl
    custo_total_brl    = custo_variavel_brl + INFRA_FIXA_BRL
    custo_por_cliente  = custo_total_brl / n_clientes if n_clientes else 0

    return {
        "n_clientes":         n_clientes,
        "gb_total_mes":       round(gb_total, 2),
        "tb_total_mes":       round(tb_total, 4),
        "tb_pago":            round(tb_pago, 4),
        "custo_bq_usd":       round(custo_bq_usd, 4),
        "custo_bq_brl":       round(custo_bq_brl, 2),
        "custo_claude_usd":   round(custo_claude_usd, 2),
        "custo_claude_brl":   round(custo_claude_brl, 2),
        "infra_fixa_brl":     INFRA_FIXA_BRL,
        "custo_total_brl":    round(custo_total_brl, 2),
        "custo_por_cliente":  round(custo_por_cliente, 2),
    }

cenarios = [calcular_custo(n) for n in range(1, 21)]

# ── Workbook ─────────────────────────────────────────────────────────────────
wb = openpyxl.Workbook()

# ── Aba 1: Tabela de dados ───────────────────────────────────────────────────
ws = wb.active
ws.title = "Dados"

HDR_FILL  = PatternFill("solid", fgColor="1E3A5F")
HDR_FONT  = Font(bold=True, color="FFFFFF", size=10)
ALT_FILL  = PatternFill("solid", fgColor="EBF2FB")
BDR       = Border(
    left=Side(style="thin", color="CCCCCC"),
    right=Side(style="thin", color="CCCCCC"),
    top=Side(style="thin", color="CCCCCC"),
    bottom=Side(style="thin", color="CCCCCC"),
)

headers = [
    ("Clientes", 10),
    ("GB/mês BQ", 12),
    ("TB/mês BQ", 12),
    ("TB pago (>1TB free)", 18),
    ("Custo BQ (US$)", 16),
    ("Custo BQ (R$)", 14),
    ("Custo Claude (US$)", 18),
    ("Custo Claude (R$)", 16),
    ("Infra Fixa (R$)", 16),
    ("Custo Total (R$)", 16),
    ("Custo/Cliente (R$)", 18),
]

for col, (h, w) in enumerate(headers, 1):
    c = ws.cell(row=1, column=col, value=h)
    c.font = HDR_FONT
    c.fill = HDR_FILL
    c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    c.border = BDR
    ws.column_dimensions[get_column_letter(col)].width = w

ws.row_dimensions[1].height = 30

for i, d in enumerate(cenarios):
    row = i + 2
    fill = ALT_FILL if i % 2 == 0 else PatternFill("solid", fgColor="FFFFFF")
    vals = [
        d["n_clientes"], d["gb_total_mes"], d["tb_total_mes"],
        d["tb_pago"], d["custo_bq_usd"], d["custo_bq_brl"],
        d["custo_claude_usd"], d["custo_claude_brl"],
        d["infra_fixa_brl"], d["custo_total_brl"], d["custo_por_cliente"],
    ]
    for col, v in enumerate(vals, 1):
        c = ws.cell(row=row, column=col, value=v)
        c.fill = fill
        c.border = BDR
        c.alignment = Alignment(horizontal="center")
        if col in (5, 6, 7, 8, 9, 10, 11):
            c.number_format = '#,##0.00'
        elif col in (2, 3, 4):
            c.number_format = '0.0000'

ws.freeze_panes = "A2"

# Nota metodológica
ws.cell(row=24, column=1, value="Metodologia").font = Font(bold=True)
notas = [
    "• Baseline: 3 clientes ativos, tabela ads_diario = 6,82 MB, medido via dry run BigQuery em 2026-10-14",
    "• Escala linear: tabela cresce ~2,27 MB por cliente adicional",
    "• BQ free tier: 1 TB/mês de queries gratuitas. Custo apenas sobre o excedente (US$ 6,25/TB)",
    "• Claude Haiku 4.5: US$ 0,80/1M tokens input + US$ 4,00/1M tokens output",
    "• Estimativa: 450 consultas/mês base (15/dia) + syncs diários (5 syncs × 30 dias)",
    "• Infra fixa: R$ 280/mês (servidor EC2 + Redis + domínio)",
    "• Câmbio: US$ 1 = R$ 5,70",
]
for i, nota in enumerate(notas):
    ws.cell(row=25 + i, column=1, value=nota).font = Font(size=9, color="555555")
ws.merge_cells("A24:K24")
for i in range(len(notas)):
    ws.merge_cells(f"A{25+i}:K{25+i}")

# ── Aba 2: Gráfico ───────────────────────────────────────────────────────────
wc = wb.create_sheet("Gráfico Custo × Clientes")

# Cabeçalho da aba gráfico
wc["A1"] = "Nº Clientes"
wc["B1"] = "Custo Total (R$)"
wc["C1"] = "Custo por Cliente (R$)"

for i, d in enumerate(cenarios):
    r = i + 2
    wc.cell(r, 1, d["n_clientes"])
    wc.cell(r, 2, d["custo_total_brl"])
    wc.cell(r, 3, d["custo_por_cliente"])

# Gráfico 1: Custo Total × Nº Clientes
chart1 = LineChart()
chart1.title = "Agente de Performance — Custo Total × Nº de Clientes"
chart1.style = 10
chart1.y_axis.title = "Custo Mensal (R$)"
chart1.x_axis.title = "Nº de Clientes"
chart1.height = 14
chart1.width  = 22

data_total = Reference(wc, min_col=2, min_row=1, max_row=21)
cats       = Reference(wc, min_col=1, min_row=2, max_row=21)
chart1.add_data(data_total, titles_from_data=True)
chart1.set_categories(cats)
chart1.series[0].graphicalProperties.line.solidFill  = "1E3A5F"
chart1.series[0].graphicalProperties.line.width      = 20000
chart1.series[0].marker.symbol = "circle"
chart1.series[0].marker.size   = 5

wc.add_chart(chart1, "E2")

# Gráfico 2: Custo por Cliente (diluição)
chart2 = LineChart()
chart2.title = "Custo por Cliente × Escala (diluição de custo fixo)"
chart2.style = 10
chart2.y_axis.title = "Custo por Cliente (R$/mês)"
chart2.x_axis.title = "Nº de Clientes"
chart2.height = 14
chart2.width  = 22

data_pc = Reference(wc, min_col=3, min_row=1, max_row=21)
chart2.add_data(data_pc, titles_from_data=True)
chart2.set_categories(cats)
chart2.series[0].graphicalProperties.line.solidFill  = "E07B2A"
chart2.series[0].graphicalProperties.line.width      = 20000
chart2.series[0].marker.symbol = "circle"
chart2.series[0].marker.size   = 5

wc.add_chart(chart2, "E20")

# Coluna widths
wc.column_dimensions["A"].width = 14
wc.column_dimensions["B"].width = 18
wc.column_dimensions["C"].width = 22

# ── Salvar ───────────────────────────────────────────────────────────────────
out = "agente_performance_custo_escala_bq.xlsx"
wb.save(out)
print(f"Salvo: {out}")

# Verificar os números chave
print(f"\nResumo:")
for n in [1, 3, 5, 10, 15, 20]:
    d = calcular_custo(n)
    print(f"  {n:2d} clientes → Total: R$ {d['custo_total_brl']:,.0f}/mês | Por cliente: R$ {d['custo_por_cliente']:,.0f}/mês | BQ: {d['gb_total_mes']:.1f} GB")
