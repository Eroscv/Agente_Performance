"""
Patch temporário — contém as novas funções de análise narrativa mensal.
Este arquivo será injetado no builder.py via script de patch.
"""

# ── Visão Mensal — análise narrativa automática ──────────────────────────────

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março",    4: "Abril",
    5: "Maio",    6: "Junho",     7: "Julho",    8: "Agosto",
    9: "Setembro",10: "Outubro",  11: "Novembro", 12: "Dezembro",
}


def _buscar_dados_diarios(data_inicio, data_fim, db_path):
    """
    Retorna lista de dicts {data, invest, compras, receita, roas} para cada dia
    do período com dados, ordenado cronologicamente.
    Reutilizada tanto pela tabela diária quanto pela narrativa mensal.
    """
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=30)
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


def _detectar_fases(dados_diarios):
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
