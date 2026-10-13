"""
relatorio/shared/analytics.py

Análises: resumo geral, alertas de ROAS, tendências, metas.
"""
import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from db.connections import LEGACY_DB_PATH as DB_PATH
from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n
from notion.blocks import h2, h3, paragrafo, divider, callout, bullet, toggle, codigo, tabela

from calendar import monthrange

logger = logging.getLogger(__name__)

from relatorio.shared.constants import DB_PATH, _SQLITE_TIMEOUT, _com_timeout
from relatorio.shared.formatters import _fmt_brl, _fmt_x, _fmt_pct, _fmt_n, _fmt_data_pt
from relatorio.regras import spend_com_imposto, calcular_cpa

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
        # aliases de normalização para compatibilidade com validadores e cron
        "investimento":     total_invest,
        "invest":           total_invest,
        "receita":          total_receita,
        "compras":          total_compras,
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


def _buscar_campanhas(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
    incluir_atribuicao: bool = True,
    cliente_key: str = None,
) -> list[dict]:
    """
    Retorna campanhas agregadas por nome no período.

    incluir_atribuicao=True  → inclui todas as campanhas (mesmo investimento=0),
                               capturando receita de atribuição cross-campaign.
    incluir_atribuicao=False → filtra AND investimento > 0, apenas campanhas ativas.

    Quando cliente_key for fornecido, usa DuckDB + campanhas_unificada (dados atuais).
    Caso contrário, faz fallback para a tabela SQLite legada `campanhas`.
    """
    filtro_atrib = "AND investimento > 0" if not incluir_atribuicao else ""

    # ── DuckDB (campanhas_unificada) ─────────────────────────────────────────
    if cliente_key:
        try:
            from db.duck import query_duck as _qd
            filtro_cliente = "AND cliente_key = ?" if cliente_key else ""
            params = (
                (data_inicio.isoformat(), data_fim.isoformat(), cliente_key)
                if cliente_key
                else (data_inicio.isoformat(), data_fim.isoformat())
            )
            sql_duck = f"""
                SELECT
                    campanha,
                    SUM(investimento)                         AS investimento,
                    SUM(COALESCE(compras, 0))                 AS compras,
                    SUM(COALESCE(impressoes, 0))              AS impressoes,
                    SUM(COALESCE(cliques_link, 0))            AS cliques_link,
                    SUM(COALESCE(cliques_todos, 0))           AS cliques_todos,
                    ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link,0)),0), 2)    AS cpc_link,
                    ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(impressoes,0)),0)*1000, 2) AS cpm,
                    SUM(COALESCE(valor_compras, 0))           AS valor_compras,
                    SUM(COALESCE(adicoes_carrinho, 0))        AS adicoes_carrinho,
                    SUM(COALESCE(checkout_iniciado, 0))       AS checkout_iniciado,
                    SUM(COALESCE(alcance, 0))                 AS alcance,
                    ROUND(CAST(SUM(COALESCE(cliques_link,0)) AS DOUBLE)
                          / NULLIF(SUM(COALESCE(impressoes,0)),0)*100, 4)   AS ctr_link,
                    ROUND(CAST(SUM(COALESCE(impressoes,0)) AS DOUBLE)
                          / NULLIF(SUM(COALESCE(alcance,0)),0), 2)          AS frequencia,
                    SUM(COALESCE(custo_por_compra, 0))        AS custo_por_compra_meta,
                    ROUND(SUM(COALESCE(valor_compras,0)) / NULLIF(SUM(investimento),0), 4) AS roas_compras
                FROM campanhas_unificada
                WHERE data_inicio >= ?
                  AND data_fim    <= ?
                  {filtro_cliente}
                  {filtro_atrib}
                GROUP BY campanha
                ORDER BY investimento DESC
            """
            return _qd(sql_duck, list(params))
        except Exception as _e:
            import logging as _log
            _log.getLogger(__name__).warning(
                "[_buscar_campanhas] DuckDB falhou, fallback SQLite: %s", _e
            )

    # ── SQLite legado ────────────────────────────────────────────────────────
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
    conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()
    cur.execute(sql, (data_inicio.isoformat(), data_fim.isoformat()))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows




def _buscar_dados_diarios(
    data_inicio: date,
    data_fim: date,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Retorna lista de dicts {data, invest, compras, receita, roas} por dia.
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


def _blocos_tendencia_mensal(data_ref: date, meses: int = 4, db_path: Path = DB_PATH) -> list[dict]:
    """
    Evolução mensal desde Jan/2026 até o mês anterior a data_ref.
    Colunas: Mês | Invest. Meta | Invest.+Imposto | Receita Meta | Receita Shopify | MER | ROAS | Compras
    Dados Meta: SQLite. Receita Shopify: Shopify API (com fallback para —).
    """
    from calendar import monthrange

    # Sempre de Jan/2026 até o mês anterior a data_ref
    ano_ini, mes_ini = 2026, 1
    ano_fim, mes_fim = data_ref.year, data_ref.month
    mes_fim -= 1
    if mes_fim == 0:
        mes_fim = 12
        ano_fim -= 1

    registros = []
    ano, mes = ano_ini, mes_ini
    while (ano, mes) <= (ano_fim, mes_fim):
        _, ultimo_dia = monthrange(ano, mes)
        ini = date(ano, mes, 1)
        fim = date(ano, mes, ultimo_dia)
        registros.append((ano, mes, ini, fim))
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1

    if not registros:
        return []

    # Busca receita Shopify em paralelo para todos os meses (reduz latência N*serial → ~1 chamada paralela)
    shopify_receitas: dict[tuple, float] = {}
    try:
        from shopify.client import receita_total as _shopify_receita
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def _fetch_shopify(ini_fim):
            ini_m, fim_m = ini_fim
            return ini_fim, _shopify_receita(ini_m, fim_m)
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(_fetch_shopify, (ini, fim)): (ini, fim) for _, _, ini, fim in registros}
            for fut in as_completed(futures):
                try:
                    key, val = fut.result()
                    shopify_receitas[key] = val
                except Exception:
                    pass
    except Exception:
        pass  # Shopify indisponível — colunas ficarão com —

    conn = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()

    serie = []
    for ano_m, mes_m, ini, fim in registros:
        cur.execute("""
            SELECT
                SUM(investimento)              AS invest,
                SUM(COALESCE(compras,0))       AS compras,
                SUM(COALESCE(valor_compras,0)) AS receita_meta
            FROM campanhas
            WHERE data_inicio >= ? AND data_fim <= ?
        """, (ini.isoformat(), fim.isoformat()))
        row          = cur.fetchone()
        invest       = float(row["invest"] or 0)
        compras      = int(row["compras"] or 0)
        receita_meta = float(row["receita_meta"] or 0)
        invest_imp   = spend_com_imposto(invest)
        roas         = round(receita_meta / invest, 2) if invest > 0 else None

        receita_shopify = shopify_receitas.get((ini, fim))
        invest_total    = invest_imp  # Google: 0 por ora
        mer = round(receita_shopify / invest_total, 2) if (receita_shopify and invest_total > 0) else None

        serie.append({
            "mes_label":       f"{_MESES_PT[mes_m]}/{ano_m}",
            "invest":          invest,
            "invest_imp":      invest_imp,
            "receita_meta":    receita_meta,
            "receita_shopify": receita_shopify,
            "mer":             mer,
            "roas":            roas,
            "compras":         compras,
        })
    conn.close()

    if not any(s["invest"] > 0 for s in serie):
        return []

    header = ["Mês", "Invest. Meta", "Invest.+Imposto", "Receita Meta", "Receita Shopify", "MER", "ROAS", "Compras"]
    table_rows = [
        {"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": h}, "annotations": {"bold": True}}] for h in header
        ]}}
    ]
    for s in serie:
        table_rows.append({"type": "table_row", "table_row": {"cells": [
            [{"text": {"content": s["mes_label"]}}],
            [{"text": {"content": _fmt_brl(s["invest"])}}],
            [{"text": {"content": _fmt_brl(s["invest_imp"])}}],
            [{"text": {"content": _fmt_brl(s["receita_meta"])}}],
            [{"text": {"content": _fmt_brl(s["receita_shopify"]) if s["receita_shopify"] is not None else "—"}}],
            [{"text": {"content": _fmt_x(s["mer"]) if s["mer"] is not None else "—"}}],
            [{"text": {"content": _fmt_x(s["roas"])}}],
            [{"text": {"content": str(s["compras"]) if s["compras"] else "—"}}],
        ]}})

    n_meses = len(serie)
    return [
        {"object": "block", "type": "heading_2",
         "heading_2": {"rich_text": [{"text": {"content": f"📅 Evolução Mensal — Jan/2026 ({n_meses} meses)"}}]}},
        {"object": "block", "type": "table", "table": {
            "table_width": len(header),
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows,
        }},
        {"object": "block", "type": "divider", "divider": {}},
    ]




