#!/usr/bin/env python3
"""
patch_builder.py — Insere as funções de análise narrativa mensal no builder.py
e refatora a seção mensal de gerar_relatorio_semanal.
"""

import re
from pathlib import Path

builder_path = Path("/home/ubuntu/.openclaw/workspace/agente-performance/relatorio/builder.py")
src = builder_path.read_text(encoding="utf-8")

# ─────────────────────────────────────────────────────────────────────────────
# 1.  NOVAS FUNÇÕES — inserir antes de def _blocos_placeholder(
# ─────────────────────────────────────────────────────────────────────────────

NOVA_SECAO = r'''# ── Visão Mensal — análise narrativa automática ──────────────────────────────


MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março",    4: "Abril",
    5: "Maio",    6: "Junho",     7: "Julho",    8: "Agosto",
    9: "Setembro",10: "Outubro",  11: "Novembro", 12: "Dezembro",
}


def _buscar_dados_diarios(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Retorna lista de dicts {data, invest, compras, receita, roas} para cada dia
    do período com dados, ordenado cronologicamente.
    Reutilizada tanto pela tabela diária quanto pela narrativa mensal.
    """
    conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            data_inicio                        AS dia,
            SUM(investimento)                  AS invest,
            SUM(COALESCE(compras, 0))           AS compras,
            SUM(COALESCE(valor_compras, 0))     AS receita
        FROM campanhas
        WHERE data_inicio >= ? AND data_fim <= ?
        GROUP BY data_inicio
        ORDER BY data_inicio
    """, (data_inicio.isoformat(), data_fim.isoformat()))
    rows = cur.fetchall()
    conn.close()

    resultado = []
    for row in rows:
        invest  = float(row["invest"] or 0)
        compras = int(row["compras"] or 0)
        receita = float(row["receita"] or 0)
        roas    = round(receita / invest, 4) if invest > 0 else None
        resultado.append({
            "data":    row["dia"],
            "invest":  invest,
            "compras": compras,
            "receita": receita,
            "roas":    roas,
        })
    return resultado


def _detectar_fases(dados_diarios: list[dict]) -> dict:
    """
    Analisa os dados diários do mês e detecta padrões em duas fases.

    Retorna dict com:
      fase1, fase2: {invest_medio, roas_medio, n_dias}
      pico_roas:    {data, valor}
      vale_roas:    {data, valor}
      faixa_eficiente: {invest_min, invest_max}  (dias com roas > 4.0)
      invest_medio_geral: float
      mediana_invest: float
      roas_baixo_invest: float  (dias com invest < mediana -> roas medio)
      roas_alto_invest: float   (dias com invest >= mediana -> roas medio)
      threshold: float          (mediana do investimento diario)
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

    # Pico e vale de ROAS (apenas dias com roas calculado)
    dias_com_roas = [d for d in dados_diarios if d["roas"] is not None]
    pico = max(dias_com_roas, key=lambda d: d["roas"]) if dias_com_roas else None
    vale = min(dias_com_roas, key=lambda d: d["roas"]) if dias_com_roas else None

    # Mediana do investimento diario (threshold para correlacao)
    invests = sorted(d["invest"] for d in dados_diarios)
    if len(invests) % 2 == 0:
        mediana = (invests[len(invests)//2 - 1] + invests[len(invests)//2]) / 2
    else:
        mediana = invests[len(invests)//2]

    # Correlacao: dias baixo invest vs alto invest
    dias_baixo = [d for d in dados_diarios if d["invest"] < mediana and d["roas"] is not None]
    dias_alto  = [d for d in dados_diarios if d["invest"] >= mediana and d["roas"] is not None]
    roas_baixo = round(sum(d["roas"] for d in dias_baixo) / len(dias_baixo), 2) if dias_baixo else None
    roas_alto  = round(sum(d["roas"] for d in dias_alto)  / len(dias_alto),  2) if dias_alto  else None

    # Faixa eficiente (invest onde roas > 4.0)
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
        "pico_roas":           {"data": pico["data"], "valor": pico["roas"]} if pico else None,
        "vale_roas":           {"data": vale["data"], "valor": vale["roas"]} if vale else None,
        "faixa_eficiente":     faixa_ef,
        "invest_medio_geral":  invest_medio_geral,
        "mediana_invest":      round(mediana, 0),
        "roas_baixo_invest":   roas_baixo,
        "roas_alto_invest":    roas_alto,
        "threshold":           round(mediana, 0),
        "n_dias_baixo":        len(dias_baixo),
        "n_dias_alto":         len(dias_alto),
    }


def _gerar_texto_narrativo_mensal(
    resumo: dict,
    dados_diarios: list[dict],
    nome_mes: str,
    ano: int,
) -> tuple:
    """
    Gera textos narrativos analíticos para a seção RESUMO do relatório mensal.
    Retorna tupla (abertura, comportamento, sensibilidade).
    """
    r = resumo
    fases = _detectar_fases(dados_diarios)

    invest      = r.get("total_invest", 0)
    invest_imp  = r.get("invest_imp", 0)
    receita     = r.get("total_receita", 0)
    compras     = r.get("total_compras", 0)
    roas        = r.get("roas")
    n_dias      = len(dados_diarios)

    # ── Parágrafo de abertura ─────────────────────────────────────────────
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

    # ── Comportamento ao longo do mês ─────────────────────────────────────
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
                f"{f1['roas_medio']:.2f}x → {f2['roas_medio']:.2f}x."
            )

    if fases.get("pico_roas") and fases.get("vale_roas"):
        pico = fases["pico_roas"]
        vale = fases["vale_roas"]
        try:
            from datetime import date as _date
            pico_d = _date.fromisoformat(pico["data"]).strftime("%d/%m")
            vale_d = _date.fromisoformat(vale["data"]).strftime("%d/%m")
        except Exception:
            pico_d = pico["data"]
            vale_d = vale["data"]
        comportamento_partes.append(
            f"Melhor dia: {pico_d} com ROAS {pico['valor']:.2f}x. "
            f"Pior dia: {vale_d} com ROAS {vale['valor']:.2f}x."
        )

    comportamento = (
        " ".join(comportamento_partes)
        if comportamento_partes
        else "Análise de comportamento diário indisponível."
    )

    # ── Sensibilidade ao investimento ─────────────────────────────────────
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
                    f"— atenção ao ponto de saturação."
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
    dados_diarios: list[dict],
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


'''

# ─────────────────────────────────────────────────────────────────────────────
# 2.  REFATORAR _blocos_visao_mensal_diaria para aceitar dados já calculados
# ─────────────────────────────────────────────────────────────────────────────

OLD_DIARIA_SIG = '''def _blocos_visao_mensal_diaria(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Seção 2 — Visão Mensal (tabela dia a dia).
    Agrega campanhas por dia e retorna tabela Notion com uma linha por dia + total.
    """
    conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            data_inicio                        AS dia,
            SUM(investimento)                  AS invest,
            SUM(COALESCE(compras, 0))           AS compras,
            SUM(COALESCE(valor_compras, 0))     AS receita
        FROM campanhas
        WHERE data_inicio >= ? AND data_fim <= ?
        GROUP BY data_inicio
        ORDER BY data_inicio
    """, (data_inicio.isoformat(), data_fim.isoformat()))
    rows = cur.fetchall()
    conn.close()

    header = ["Data", "Gasto", "Compras Meta", "Receita Meta", "ROAS Meta", "Ticket Médio Meta"]'''

NEW_DIARIA_SIG = '''def _blocos_visao_mensal_diaria(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
    dados_diarios: list = None,
) -> list[dict]:
    """
    Seção 2 — Visão Mensal (tabela dia a dia).
    Se dados_diarios fornecidos (lista de dicts), usa-os diretamente.
    Caso contrário busca no SQLite.
    """
    if dados_diarios is None:
        dados_diarios = _buscar_dados_diarios(data_inicio, data_fim, db_path)

    # Converte dados_diarios para formato compatível com o loop original
    class _Row:
        def __init__(self, d):
            self._d = d
        def __getitem__(self, k):
            return self._d.get(k)

    rows_wrapped = [_Row({
        "dia":     d["data"],
        "invest":  d["invest"],
        "compras": d["compras"],
        "receita": d["receita"],
    }) for d in dados_diarios]
    rows = rows_wrapped

    header = ["Data", "Gasto", "Compras Meta", "Receita Meta", "ROAS Meta", "Ticket Médio Meta"]'''

# Refatorar o loop interno da funcao (remover rows de sqlite, usar lista direta)
OLD_LOOP = '''    for row in rows:
        invest  = float(row["invest"] or 0)
        compras = int(row["compras"] or 0)
        receita = float(row["receita"] or 0)
        roas    = round(receita / invest, 2) if invest > 0 else None
        ticket  = round(receita / compras, 2) if compras else None

        # Formatar data como dd/mm
        try:
            dia_date = date.fromisoformat(row["dia"])
            dia_str  = dia_date.strftime("%d/%m")
        except Exception:
            dia_str = str(row["dia"])'''

NEW_LOOP = '''    for row in rows:
        invest  = float(row["invest"] or 0)
        compras = int(row["compras"] or 0)
        receita = float(row["receita"] or 0)
        roas    = round(receita / invest, 2) if invest > 0 else None
        ticket  = round(receita / compras, 2) if compras else None

        # Formatar data como dd/mm
        try:
            from datetime import date as _date_cls
            dia_val = row["dia"]
            if isinstance(dia_val, _date_cls):
                dia_str = dia_val.strftime("%d/%m")
            else:
                dia_str = _date_cls.fromisoformat(str(dia_val)).strftime("%d/%m")
        except Exception:
            dia_str = str(row["dia"])'''

OLD_IFROWS = '''    # Se não houver dados, pelo menos 1 linha de dados (regra Notion)
    if not rows:'''

NEW_IFROWS = '''    # Se não houver dados, pelo menos 1 linha de dados (regra Notion)
    if not rows_wrapped:
        rows = []
    if not rows:'''


# ─────────────────────────────────────────────────────────────────────────────
# 3.  REFATORAR gerar_relatorio_semanal: seção mensal — inserir narrativa
#     e passar dados_diarios para a tabela
# ─────────────────────────────────────────────────────────────────────────────

OLD_MENSAL_BLOCOS = '''        # 1. Análise da última semana
        blocos += _blocos_analise_ultima_semana(data_inicio, data_fim, db_path)

        # 2. Visão mensal dia a dia
        blocos += _blocos_visao_mensal_diaria(data_inicio, data_fim, db_path)'''

NEW_MENSAL_BLOCOS = '''        # 1. Análise da última semana
        blocos += _blocos_analise_ultima_semana(data_inicio, data_fim, db_path)

        # 1b. Buscar dados diários (compartilhado pela narrativa e pela tabela)
        _dados_diarios_mes = _buscar_dados_diarios(data_inicio, data_fim, db_path)
        _nome_mes_pt = MESES_PT[data_inicio.month]

        # 2a. Visão Mensal — narrativa estratégica (RESUMO, fases, correlação)
        blocos += _blocos_visao_mensal_narrativa(resumo, _dados_diarios_mes, _nome_mes_pt, data_inicio.year)

        # 2b. Visão mensal dia a dia (tabela — reutiliza dados já buscados)
        blocos += _blocos_visao_mensal_diaria(data_inicio, data_fim, db_path, dados_diarios=_dados_diarios_mes)'''


# ─────────────────────────────────────────────────────────────────────────────
# Aplicar patches
# ─────────────────────────────────────────────────────────────────────────────

# Inserir novas funções antes de _blocos_placeholder
ANCHOR_PLACEHOLDER = "def _blocos_placeholder("
assert ANCHOR_PLACEHOLDER in src, "Âncora _blocos_placeholder não encontrada!"
src = src.replace(ANCHOR_PLACEHOLDER, NOVA_SECAO + ANCHOR_PLACEHOLDER, 1)

# Refatorar assinatura de _blocos_visao_mensal_diaria
assert OLD_DIARIA_SIG in src, "Âncora OLD_DIARIA_SIG não encontrada!"
src = src.replace(OLD_DIARIA_SIG, NEW_DIARIA_SIG, 1)

# Refatorar loop interno (formato de data)
assert OLD_LOOP in src, "Âncora OLD_LOOP não encontrada!"
src = src.replace(OLD_LOOP, NEW_LOOP, 1)

# Refatorar verificação if not rows
assert OLD_IFROWS in src, "Âncora OLD_IFROWS não encontrada!"
src = src.replace(OLD_IFROWS, NEW_IFROWS, 1)

# Refatorar gerar_relatorio_semanal — seção mensal
assert OLD_MENSAL_BLOCOS in src, "Âncora OLD_MENSAL_BLOCOS não encontrada!"
src = src.replace(OLD_MENSAL_BLOCOS, NEW_MENSAL_BLOCOS, 1)

builder_path.write_text(src, encoding="utf-8")
print("✅ Patch aplicado com sucesso!")
print(f"   Tamanho final: {len(src):,} bytes")
