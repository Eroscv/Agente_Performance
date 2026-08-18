"""
agent/comparatives.py

Consultas de comparativos do Agente de Performance Guará.

Tipos de comparativo disponíveis:

1. comparar_periodos(periodo_a, periodo_b, campanha?)
   → Compara dois períodos lado a lado com variação % em todas as métricas

2. comparar_campanhas_detalhado(campanhas, data_inicio?, data_fim?)
   → Comparação rica entre campanhas com ranking por métrica e destaque do vencedor

3. comparar_semana_a_semana(campanha?, semanas?)
   → Últimas N semanas com variação WoW (week over week)

4. comparar_mes_a_mes(campanha?, meses?)
   → Últimos N meses com variação MoM (month over month)

5. comparar_antes_depois(campanha, data_virada)
   → Divide a série histórica em antes/depois de uma data

6. ranking_comparativo(metrica, data_inicio?, data_fim?)
   → Ranking completo de todas as campanhas por qualquer métrica
"""

import logging
from datetime import date, timedelta
from typing import Optional

from db.connection import query, query_one

logger = logging.getLogger(__name__)

# ── Métricas disponíveis para ranking ─────────────────────────────────────────

METRICAS_DISPONIVEIS = {
    "roas":             ("valor_compras",   "SUM_DIV_INVEST", "ROAS"),
    "investimento":     ("investimento",    "SUM", "Investimento Total (R$)"),
    "receita":          ("valor_compras",   "SUM", "Receita Total (R$)"),
    "compras":          ("compras",         "SUM", "Compras"),
    "cpc":              ("cpc_link",        "DIV_INVEST_CLIQUES", "CPC (R$)"),
    "ctr":              ("ctr_link",        "DIV_CLIQUES_IMPRESS", "CTR (%)"),
    "cpm":              ("cpm",             "DIV_INVEST_CPM", "CPM (R$)"),
    "frequencia":       ("frequencia",      "AVG", "Frequência Média"),
    "impressoes":       ("impressoes",      "SUM", "Impressões"),
    "alcance":          ("alcance",         "SUM", "Alcance"),
    "custo_por_compra": ("custo_por_compra","CPA_CALC", "Custo por Compra (R$)"),
    "cliques":          ("cliques_link",    "SUM", "Cliques no Link"),
    "carrinho":         ("adicoes_carrinho","SUM", "Adições ao Carrinho"),
    "checkout":         ("checkout_iniciado","SUM","Checkout Iniciado"),
}


# ── 1. Comparar dois períodos ─────────────────────────────────────────────────

def comparar_periodos(
    inicio_a: str,
    fim_a:    str,
    inicio_b: str,
    fim_b:    str,
    campanha: Optional[str] = None,
) -> dict:
    """
    Compara dois períodos com variação % em todas as métricas principais.

    Returns:
        {
            "periodo_a": { label, metricas... },
            "periodo_b": { label, metricas... },
            "variacao":  { metrica: { valor_a, valor_b, diff, pct, tendencia } }
        }
    """
    filtro_camp = "AND campanha = ?" if campanha else ""

    def _buscar(ini, fim):
        params = [ini, fim] + ([campanha] if campanha else [])
        return query_one(f"""
            SELECT
                COUNT(DISTINCT campanha)               AS campanhas,
                ROUND(SUM(investimento), 2)            AS investimento,
                ROUND(SUM(COALESCE(valor_compras, 0)), 2)           AS receita,
                ROUND(SUM(COALESCE(compras, 0)), 0)                 AS compras,
                ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4) AS roas,
                ROUND(SUM(impressoes), 0)              AS impressoes,
                ROUND(SUM(alcance), 0)                 AS alcance,
                ROUND(SUM(COALESCE(cliques_link, 0)), 0)            AS cliques,
                ROUND(AVG(ctr_link), 4)                AS ctr,
                ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc,
                ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2) AS cpm,
                ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS custo_por_compra,
                ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia,
                ROUND(SUM(COALESCE(adicoes_carrinho, 0)), 0)        AS carrinho,
                ROUND(SUM(COALESCE(checkout_iniciado, 0)), 0)       AS checkout
            FROM campanhas_unificada
            WHERE data_inicio >= ? AND data_fim <= ?
            {filtro_camp}
        """, tuple(params)) or {}

    dados_a = _buscar(inicio_a, fim_a)
    dados_b = _buscar(inicio_b, fim_b)

    metricas_chave = [
        "investimento", "receita", "compras", "roas", "impressoes",
        "alcance", "cliques", "ctr", "cpc", "cpm",
        "custo_por_compra", "frequencia", "carrinho", "checkout",
    ]

    # Métricas onde MENOR é melhor (variação negativa = positiva)
    menor_melhor = {"cpc", "cpm", "custo_por_compra", "frequencia"}

    variacao = {}
    for m in metricas_chave:
        va = dados_a.get(m)
        vb = dados_b.get(m)
        if va is None or vb is None or va == 0:
            variacao[m] = {"valor_a": va, "valor_b": vb, "diff": None, "pct": None, "tendencia": "—"}
            continue

        diff = round((vb or 0) - va, 4)
        pct  = round(diff / va * 100, 1) if va != 0 else None

        # Tendência: ↑↓→ considerando se menor é melhor
        if pct is None or abs(pct) < 1:
            tendencia = "→ estável"
        elif m in menor_melhor:
            tendencia = "✅ melhorou" if diff < 0 else "⚠️ piorou"
        else:
            tendencia = "✅ melhorou" if diff > 0 else "⚠️ piorou"

        variacao[m] = {
            "valor_a":   va,
            "valor_b":   vb,
            "diff":      diff,
            "pct":       pct,
            "tendencia": tendencia,
        }

    return {
        "periodo_a":   {"label": f"{inicio_a} a {fim_a}", **dados_a},
        "periodo_b":   {"label": f"{inicio_b} a {fim_b}", **dados_b},
        "variacao":    variacao,
        "campanha":    campanha,
    }


# ── 2. Comparar campanhas detalhado ───────────────────────────────────────────

def comparar_campanhas_detalhado(
    campanhas:   list[str],
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> dict:
    """
    Comparação rica entre campanhas com ranking por métrica.
    Retorna vencedor em cada categoria.
    """
    if not campanhas:
        return {}

    placeholders = ", ".join(["?"] * len(campanhas))
    filtro_data  = ""
    params       = list(campanhas)

    if data_inicio:
        filtro_data += " AND data_inicio >= ?"
        params.append(data_inicio)
    if data_fim:
        filtro_data += " AND data_fim <= ?"
        params.append(data_fim)

    rows = query(f"""
        SELECT
            campanha,
            ROUND(SUM(investimento), 2)          AS investimento,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2)          AS receita,
            ROUND(SUM(COALESCE(compras, 0)), 0)                 AS compras,
            ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4) AS roas,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc,
            ROUND(CAST(SUM(COALESCE(cliques_link, 0)) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4) AS ctr,
            ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2) AS cpm,
            ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS custo_por_compra,
            ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia,
            ROUND(SUM(impressoes), 0)             AS impressoes,
            ROUND(SUM(alcance), 0)                AS alcance,
            ROUND(SUM(COALESCE(cliques_link, 0)), 0)           AS cliques,
            ROUND(SUM(COALESCE(adicoes_carrinho, 0)), 0)       AS carrinho,
            ROUND(SUM(COALESCE(checkout_iniciado, 0)), 0)      AS checkout,
            MIN(data_inicio)                      AS periodo_inicio,
            MAX(data_fim)                         AS periodo_fim
        FROM campanhas_unificada
        WHERE campanha IN ({placeholders})
        {filtro_data}
        GROUP BY campanha
        ORDER BY investimento DESC
    """, tuple(params))

    if not rows:
        return {"campanhas": [], "vencedores": {}}

    # Determina vencedor em cada métrica
    maior_melhor = ["receita", "compras", "roas", "ctr", "impressoes", "alcance", "cliques", "carrinho", "checkout"]
    menor_melhor = ["cpc", "cpm", "custo_por_compra", "frequencia"]

    vencedores = {}
    for metrica in maior_melhor:
        melhor = max(rows, key=lambda r: r.get(metrica) or 0)
        vencedores[metrica] = melhor["campanha"]

    for metrica in menor_melhor:
        validos = [r for r in rows if r.get(metrica) and r[metrica] > 0]
        if validos:
            melhor = min(validos, key=lambda r: r[metrica])
            vencedores[metrica] = melhor["campanha"]

    return {
        "campanhas":  rows,
        "vencedores": vencedores,
        "periodo":    {"inicio": data_inicio, "fim": data_fim},
    }


# ── 3. Semana a semana (WoW) ──────────────────────────────────────────────────

def comparar_semana_a_semana(
    campanha: Optional[str] = None,
    semanas:  int = 6,
) -> list[dict]:
    """
    Retorna últimas N semanas com variação WoW (week over week).
    """
    hoje  = date.today()
    filtro_camp  = "AND campanha = ?" if campanha else ""
    resultado    = []
    semana_ant   = None

    for i in range(semanas - 1, -1, -1):
        fim_sem = hoje - timedelta(days=hoje.weekday() + 7 * i)
        ini_sem = fim_sem - timedelta(days=6)

        params = [ini_sem.isoformat(), fim_sem.isoformat()] + ([campanha] if campanha else [])
        row = query_one(f"""
            SELECT
                ROUND(SUM(investimento), 2)     AS investimento,
                ROUND(SUM(COALESCE(valor_compras, 0)), 2)    AS receita,
                ROUND(SUM(COALESCE(compras, 0)), 0)          AS compras,
                ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4) AS roas,
                ROUND(SUM(COALESCE(cliques_link, 0)), 0)     AS cliques,
                ROUND(AVG(ctr_link), 4)         AS ctr,
                ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc
            FROM campanhas_unificada
            WHERE data_inicio >= ? AND data_fim <= ?
            {filtro_camp}
        """, tuple(params)) or {}

        semana = {
            "semana":     f"{ini_sem.strftime('%d/%m')} – {fim_sem.strftime('%d/%m/%Y')}",
            "data_inicio": ini_sem.isoformat(),
            "data_fim":    fim_sem.isoformat(),
            **row,
        }

        # Variação WoW
        if semana_ant:
            for m in ["investimento", "receita", "compras", "roas", "cliques", "ctr"]:
                va = semana_ant.get(m)
                vb = row.get(m)
                if va and vb and va != 0:
                    pct = round((vb - va) / va * 100, 1)
                    semana[f"{m}_wow_pct"] = pct
                else:
                    semana[f"{m}_wow_pct"] = None

        resultado.append(semana)
        semana_ant = row

    return resultado


# ── 4. Mês a mês (MoM) ───────────────────────────────────────────────────────

def comparar_mes_a_mes(
    campanha: Optional[str] = None,
    meses:    int = 6,
) -> list[dict]:
    """
    Retorna últimos N meses com variação MoM (month over month).
    """
    import calendar as cal

    hoje         = date.today()
    filtro_camp  = "AND campanha = ?" if campanha else ""
    resultado    = []
    mes_ant      = None

    for i in range(meses - 1, -1, -1):
        # Calcula mês relativo
        ano  = hoje.year
        mes  = hoje.month - i
        while mes <= 0:
            mes += 12
            ano -= 1

        ini_mes = date(ano, mes, 1)
        fim_mes = date(ano, mes, cal.monthrange(ano, mes)[1])

        params = [ini_mes.isoformat(), fim_mes.isoformat()] + ([campanha] if campanha else [])
        row = query_one(f"""
            SELECT
                ROUND(SUM(investimento), 2)     AS investimento,
                ROUND(SUM(COALESCE(valor_compras, 0)), 2)    AS receita,
                ROUND(SUM(COALESCE(compras, 0)), 0)          AS compras,
                ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4) AS roas,
                ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc,
                ROUND(AVG(ctr_link), 4)         AS ctr,
                ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2) AS cpm,
                ROUND(SUM(impressoes), 0)       AS impressoes,
                ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS custo_por_compra
            FROM campanhas_unificada
            WHERE data_inicio >= ? AND data_fim <= ?
            {filtro_camp}
        """, tuple(params)) or {}

        nomes_mes = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
        mes_dict  = {
            "mes":        f"{nomes_mes[mes-1]}/{str(ano)[2:]}",
            "data_inicio": ini_mes.isoformat(),
            "data_fim":    fim_mes.isoformat(),
            **row,
        }

        # Variação MoM
        if mes_ant:
            for m in ["investimento", "receita", "compras", "roas", "cpc", "cpm"]:
                va = mes_ant.get(m)
                vb = row.get(m)
                if va and vb and va != 0:
                    pct = round((vb - va) / va * 100, 1)
                    mes_dict[f"{m}_mom_pct"] = pct
                else:
                    mes_dict[f"{m}_mom_pct"] = None

        resultado.append(mes_dict)
        mes_ant = row

    return resultado


# ── 5. Antes vs Depois de uma data ───────────────────────────────────────────

def comparar_antes_depois(
    data_virada: str,
    campanha:    Optional[str] = None,
) -> dict:
    """
    Divide o histórico em antes/depois de uma data e compara.
    Útil para medir impacto de mudanças na campanha.
    """
    filtro_camp = "AND campanha = ?" if campanha else ""
    params_base = [campanha] if campanha else []

    def _buscar(operador):
        params = [data_virada] + params_base
        return query_one(f"""
            SELECT
                COUNT(DISTINCT DATE(data_inicio))      AS dias,
                ROUND(SUM(investimento), 2)            AS investimento,
                ROUND(SUM(COALESCE(valor_compras, 0)), 2)           AS receita,
                ROUND(SUM(COALESCE(compras, 0)), 0)                 AS compras,
                ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4) AS roas,
                ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc,
                ROUND(AVG(ctr_link), 4)                AS ctr,
                ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2) AS cpm,
                ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS custo_por_compra,
                ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia,
                ROUND(SUM(impressoes), 0)              AS impressoes,
                MIN(data_inicio)                       AS data_inicio_real,
                MAX(data_fim)                          AS data_fim_real
            FROM campanhas_unificada
            WHERE data_inicio {operador} ?
            {filtro_camp}
        """, tuple(params)) or {}

    antes  = _buscar("<")
    depois = _buscar(">=")

    # Variações
    metricas_comparar = ["investimento", "receita", "compras", "roas", "cpc", "ctr", "cpm", "custo_por_compra"]
    menor_melhor      = {"cpc", "cpm", "custo_por_compra", "frequencia"}
    variacao          = {}

    for m in metricas_comparar:
        va = antes.get(m)
        vb = depois.get(m)
        if va and vb and va != 0:
            diff = round(vb - va, 4)
            pct  = round(diff / va * 100, 1)
            tend = ("✅" if diff < 0 else "⚠️") if m in menor_melhor else ("✅" if diff > 0 else "⚠️")
            variacao[m] = {"antes": va, "depois": vb, "diff": diff, "pct": pct, "tendencia": tend}
        else:
            variacao[m] = {"antes": va, "depois": vb, "diff": None, "pct": None, "tendencia": "—"}

    return {
        "data_virada": data_virada,
        "campanha":    campanha,
        "antes":       antes,
        "depois":      depois,
        "variacao":    variacao,
    }


# ── 6. Ranking comparativo por métrica ───────────────────────────────────────

def ranking_comparativo(
    metrica:     str = "roas",
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """
    Ranking de todas as campanhas por qualquer métrica.
    """
    cfg = METRICAS_DISPONIVEIS.get(metrica, METRICAS_DISPONIVEIS["roas"])
    col, func, label = cfg

    filtro_data  = ""
    params       = []
    if data_inicio:
        filtro_data += " AND data_inicio >= ?"
        params.append(data_inicio)
    if data_fim:
        filtro_data += " AND data_fim <= ?"
        params.append(data_fim)

    menor_melhor = metrica in {"cpc", "cpm", "custo_por_compra", "frequencia"}
    ordem        = "ASC" if menor_melhor else "DESC"

    # ROAS usa fórmula especial: Σ receita / Σ investimento
    if func == "SUM_DIV_INVEST":
        expr_metrica = "ROUND(SUM(valor_compras) / NULLIF(SUM(investimento), 0), 4)"
    elif func == "DIV_INVEST_CLIQUES":
        expr_metrica = "ROUND(SUM(investimento) / NULLIF(SUM(cliques_link), 0), 2)"
    elif func == "DIV_CLIQUES_IMPRESS":
        expr_metrica = "ROUND(CAST(SUM(cliques_link) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4)"
    elif func == "DIV_INVEST_CPM":
        expr_metrica = "ROUND(SUM(investimento) / NULLIF(SUM(impressoes), 0) * 1000, 2)"
    elif func == "CPA_CALC":
        expr_metrica = "ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(compras), 0), 2)"
    elif func == "AVG":
        expr_metrica = f"ROUND(AVG({col}), 4)"
    else:
        expr_metrica = f"ROUND(SUM({col}), 4)"

    rows = query(f"""
        SELECT
            campanha,
            {expr_metrica}                       AS valor_metrica,
            ROUND(SUM(investimento), 2)          AS investimento,
            ROUND(SUM(valor_compras), 2)         AS receita,
            ROUND(SUM(compras), 0)               AS compras,
            ROUND(SUM(valor_compras) / NULLIF(SUM(investimento), 0), 4) AS roas,
            COUNT(DISTINCT data_inicio)          AS dias_ativos
        FROM campanhas_unificada
        WHERE campanha IS NOT NULL AND campanha != ''
        {filtro_data}
        GROUP BY campanha
        HAVING valor_metrica IS NOT NULL AND SUM(investimento) >= 100
        ORDER BY valor_metrica {ordem}
    """, tuple(params))

    for i, row in enumerate(rows, 1):
        row["posicao"]         = i
        row["metrica_nome"]    = label
        row["metrica_chave"]   = metrica

    return rows
