"""
agent/queries/evolucao.py

Evolução diária, variação de ROAS e comparativos de período.
"""
import logging
from typing import Optional

from db.connection import query, query_one, DB_PATH
from utils.formatters import fmt_brl

logger = logging.getLogger(__name__)

from agent.queries.base import _build_where, _meta_ads_union_source, _meta_ads_where, _ROAS_EXPR

def evolucao_diaria(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """Evolução diária agregada (todas as campanhas ou uma específica)."""
    base = "campanha = ?" if campanha else None
    extra = [campanha] if campanha else []
    where, params = _build_where(
        base,
        extra_params=extra,
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            data_inicio                            AS data,
            ROUND(SUM(investimento), 2)            AS investimento,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2)           AS receita,
            ROUND(SUM(COALESCE(compras, 0)), 0)                  AS compras,
            {_ROAS_EXPR}                                         AS roas,
            ROUND(SUM(impressoes), 0)                            AS impressoes,
            ROUND(SUM(COALESCE(cliques_link, 0)), 0)             AS cliques,
            ROUND(CAST(SUM(COALESCE(cliques_link, 0)) AS FLOAT) / NULLIF(SUM(impressoes), 0) * 100, 4) AS ctr_medio
        FROM campanhas_unificada
        {where}
        GROUP BY data_inicio
        ORDER BY data_inicio ASC
    """, tuple(params))



def variacao_diaria_roas(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """
    Volatilidade do ROAS dia a dia.

    Retorna ROAS diário e variação percentual em relação ao dia anterior.
    Útil para identificar picos, quedas e instabilidades.
    """
    base  = "campanha = ?" if campanha else None
    extra = [campanha] if campanha else []
    where, params = _build_where(
        base,
        extra_params=extra,
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            data_inicio                         AS data,
            ROUND(SUM(investimento), 2)         AS investimento,
            ROUND(SUM(COALESCE(valor_compras, 0)), 2) AS receita,
            ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4) AS roas,
            ROUND(
                (SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0)
                - LAG(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0))
                    OVER (ORDER BY data_inicio))
                / NULLIF(
                    LAG(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0))
                    OVER (ORDER BY data_inicio),
                    0
                ) * 100
            , 1) AS variacao_roas_pct,
            ROUND(SUM(COALESCE(compras, 0)), 0) AS compras
        FROM campanhas_unificada
        {where}
        GROUP BY data_inicio
        ORDER BY data_inicio ASC
    """, tuple(params))



def comparar_campanhas(
    campanhas:   list[str],
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """Compara múltiplas campanhas lado a lado."""
    if not campanhas:
        return []
    placeholders = ", ".join(["?"] * len(campanhas))
    where, params = _build_where(
        f"campanha IN ({placeholders})",
        extra_params=list(campanhas),
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            campanha,
            ROUND(SUM(investimento), 2)          AS investimento,
            ROUND(SUM(valor_compras), 2)          AS receita,
            ROUND(SUM(compras), 0)                AS compras,
            {_ROAS_EXPR}                          AS roas,
            ROUND(SUM(investimento) / NULLIF(SUM(COALESCE(cliques_link, 0)), 0), 2) AS cpc_medio,
            ROUND(AVG(ctr_link), 4)               AS ctr_medio,
            ROUND(SUM(investimento) * 1.1215 / NULLIF(SUM(COALESCE(compras, 0)), 0), 2) AS cpa_ajustado,
            ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia_media,
            ROUND(SUM(impressoes), 0)             AS impressoes,
            ROUND(SUM(alcance), 0)                AS alcance,
            MIN(data_inicio)                      AS data_inicio,
            MAX(data_fim)                         AS data_fim
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        ORDER BY investimento DESC
    """, tuple(params))



def comparar_mesmo_periodo_ano_anterior(
    data_inicio: str,
    data_fim:    str,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> dict:
    """
    Compara o período solicitado com o mesmo período do ano anterior.
    Útil para detectar sazonalidade (Black Friday, Dia das Mães, etc).
    Retorna dict com periodo_atual, periodo_anterior_ano e deltas percentuais.
    """
    from datetime import date, timedelta

    try:
        ini = date.fromisoformat(data_inicio)
        fim = date.fromisoformat(data_fim)
    except Exception:
        return {"aviso": "Datas inválidas para comparação anual."}

    # Mesmo período, ano anterior
    ini_ano_ant = ini.replace(year=ini.year - 1)
    fim_ano_ant = fim.replace(year=fim.year - 1)

    fk: dict = {}
    if cliente_key:
        fk["cliente_key"] = cliente_key
    if plataforma:
        fk["plataforma"] = plataforma

    def _resumo(di: str, df: str) -> dict:
        where, params = _build_where(
            None,
            data_inicio=di,
            data_fim=df,
            **fk,
        )
        row = query_one(f"""
            SELECT
                ROUND(SUM(investimento), 2)                AS investimento,
                ROUND(SUM(valor_compras), 2)               AS receita,
                ROUND(SUM(compras), 0)                     AS compras,
                {_ROAS_EXPR}                               AS roas,
                ROUND(SUM(impressoes), 0)                  AS impressoes,
                ROUND(SUM(alcance), 0)                     AS alcance
            FROM campanhas_unificada
            {where}
        """, tuple(params))
        return dict(row) if row else {}

    atual     = _resumo(data_inicio, data_fim)
    anterior  = _resumo(str(ini_ano_ant), str(fim_ano_ant))

    tem_ant = bool(anterior and anterior.get("investimento"))

    # Calcula variações percentuais quando há dados do ano anterior
    variacoes: dict = {}
    if tem_ant:
        for k in ("investimento", "receita", "compras", "roas"):
            v_at  = atual.get(k)   or 0
            v_ant = anterior.get(k) or 0
            if v_ant:
                variacoes[f"var_{k}_pct"] = round((v_at - v_ant) / v_ant * 100, 2)

    result: dict = {
        "periodo_atual":           {"inicio": data_inicio, "fim": data_fim, **atual},
        "tem_dados_ano_anterior":  tem_ant,
    }
    if tem_ant:
        result["periodo_ano_anterior"] = {
            "inicio": str(ini_ano_ant),
            "fim":    str(fim_ano_ant),
            **anterior,
        }
        result["variacao"] = {
            k.replace("var_", "").replace("_pct", "_pct"): v
            for k, v in variacoes.items()
        }
        # Aliases para compatibilidade com testes
        result["variacao"]["roas_pct"]        = variacoes.get("var_roas_pct")
        result["variacao"]["investimento_pct"] = variacoes.get("var_investimento_pct")
        result["variacao"]["receita_pct"]      = variacoes.get("var_receita_pct")
        result["variacao"]["compras_pct"]      = variacoes.get("var_compras_pct")
        result.update(variacoes)
    else:
        result["aviso"] = (
            f"Não há dados do mesmo período em {ini.year - 1} "
            f"para comparação de sazonalidade."
        )

    return result


