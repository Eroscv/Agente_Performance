"""
agent/queries/base.py

Helpers internos, diagnóstico e consultas base.
"""
import logging
from typing import Optional

from db.connection import query, query_one, DB_PATH
from utils.formatters import fmt_brl

logger = logging.getLogger(__name__)

# Expressão SQL padrão para ROAS — usada em todos os submódulos de queries
_ROAS_EXPR = "ROUND(SUM(COALESCE(valor_compras, 0)) / NULLIF(SUM(investimento), 0), 4)"

# Expressão SQL para frequência média
_FREQ_EXPR = "ROUND(CAST(SUM(COALESCE(impressoes, 0)) AS FLOAT) / NULLIF(SUM(COALESCE(alcance, 0)), 0), 2) AS frequencia_media"

# Padrões de campanha awareness/tofu a excluir de rankings de conversão
AWARENESS_PATTERNS: list[str] = [
    'tráfego', 'trafego', 'awareness', 'engajamento', 'alcance', 'visitas'
]


def _build_where(
    base_condition: Optional[str],
    extra_params:   list = None,
    data_inicio:    Optional[str] = None,
    data_fim:       Optional[str] = None,
    cliente_key:    Optional[str] = None,
    plataforma:     Optional[str] = None,
) -> tuple[str, list]:
    """
    Constrói cláusula WHERE completa de forma segura.

    - base_condition: condição inicial (ex: "campanha IS NOT NULL", pode ser None)
    - extra_params: parâmetros posicionais correspondentes à base_condition
    - data_inicio / data_fim: filtros de período opcionais
    - cliente_key: filtra por cliente (ex: 'source_of', 'pod_kombucha')
    - plataforma: filtra por plataforma (ex: 'meta_ads', 'google_ads')

    Retorna (where_clause_str, params_list).
    """
    conditions = []
    params = list(extra_params or [])

    if base_condition:
        conditions.append(base_condition)

    if cliente_key:
        conditions.append("cliente_key = ?")
        params.append(cliente_key)
    if plataforma:
        conditions.append("plataforma = ?")
        params.append(plataforma)
    if data_inicio:
        conditions.append("data_inicio >= ?")
        params.append(data_inicio)
    if data_fim:
        conditions.append("data_fim <= ?")
        params.append(data_fim)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


# ── Novas consultas v3 ────────────────────────────────────────────────────────


def _meta_ads_union_source(cliente_key: Optional[str] = None) -> str:
    """
    Retorna a cláusula FROM correta para queries de criativo (ad-level).
    Usa as tabelas brutas Meta Ads pois a view campanhas_unificada não expõe ad_name/ad_id.

    Com a nova arquitetura federada, as tabelas são acessadas via schemas ATTACHados:
      sof_meta.source_of_meta_ads_diario
      pod_meta.pod_kombucha_meta_ads_diario
    """
    # Mapeamento cliente_key -> (schema_attach, tabela)
    _META_TABLES = {
        "source_of":    ("sof_meta", "source_of_meta_ads_diario"),
        "pod_kombucha": ("pod_meta", "pod_kombucha_meta_ads_diario"),
    }

    if cliente_key and cliente_key in _META_TABLES:
        schema, tabela = _META_TABLES[cliente_key]
        return f"FROM {schema}.{tabela}"

    # Sem filtro de cliente: UNION de todas as tabelas Meta
    unions = []
    for ck, (schema, tabela) in _META_TABLES.items():
        unions.append(f"""
        SELECT '{ck}' AS cliente_key, 'meta_ads' AS plataforma,
               data, campanha, campaign_id, adset_name, adset_id,
               ad_name, ad_id, objetivo, impressoes, alcance,
               cliques_link, investimento, compras, valor_compras,
               frequencia
        FROM {schema}.{tabela}
        """)
    union_sql = " UNION ALL ".join(unions)
    return f"FROM ({union_sql}) AS meta_ads_union"



def _meta_ads_where(
    base_condition: Optional[str],
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    cliente_key: Optional[str] = None,
) -> tuple[str, list]:
    """
    WHERE builder para queries diretas nas tabelas Meta Ads brutas.
    Não inclui filtro de plataforma (já é sempre meta_ads aqui).
    Não inclui cliente_key quando já está resolvido na FROM clause.
    """
    conditions = []
    params = []

    if base_condition:
        conditions.append(base_condition)

    if campanha:
        conditions.append("campanha LIKE ?")
        params.append(f"%{campanha}%")

    if data_inicio:
        conditions.append("data >= ?")
        params.append(data_inicio)
    if data_fim:
        conditions.append("data <= ?")
        params.append(data_fim)

    # cliente_key só é necessário na WHERE quando usamos UNION (FROM com subconsulta)
    if not cliente_key or cliente_key not in ("source_of", "pod_kombucha"):
        pass  # não filtra — UNION já inclui todos

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params



    def _buscar(d_ini, d_fim):
        where, params = _build_where(
            None,
            data_inicio=str(d_ini),
            data_fim=str(d_fim),
            cliente_key=cliente_key,
            plataforma=plataforma,
        )
        return query_one(f"""
            SELECT
                ROUND(SUM(investimento), 2)               AS investimento,
                ROUND(SUM(COALESCE(valor_compras,0)), 2)  AS receita,
                ROUND(SUM(COALESCE(compras,0)), 0)        AS compras,
                {_ROAS_EXPR}                              AS roas,
                ROUND(SUM(impressoes), 0)                 AS impressoes,
                ROUND(CAST(SUM(COALESCE(cliques_link,0)) AS FLOAT) / NULLIF(SUM(impressoes),0)*100, 2) AS ctr
            FROM campanhas_unificada {where}
        """, tuple(params)) or {}

    atual    = _buscar(ini, fim)
    anterior = _buscar(ini_ano_ant, fim_ano_ant)

    if not anterior.get("investimento"):
        return {
            "aviso": f"Sem dados para o mesmo período do ano anterior ({ini_ano_ant} → {fim_ano_ant}).",
            "periodo_atual": {"inicio": str(ini), "fim": str(fim), **atual},
        }


    def _pct(a, b):
        if not b or b == 0:
            return None
        return round((a - b) / b * 100, 1)

    return {
        "periodo_atual":    {"inicio": str(ini),         "fim": str(fim),         **atual},
        "periodo_anterior": {"inicio": str(ini_ano_ant), "fim": str(fim_ano_ant), **anterior},
        "variacao": {
            "investimento_pct": _pct(atual.get("investimento",0), anterior.get("investimento",0)),
            "receita_pct":      _pct(atual.get("receita",0),      anterior.get("receita",0)),
            "compras_pct":      _pct(atual.get("compras",0),      anterior.get("compras",0)),
            "roas_pct":         _pct(atual.get("roas",0),         anterior.get("roas",0)),
            "ctr_pct":          _pct(atual.get("ctr",0),          anterior.get("ctr",0)),
        },
        "tem_dados_ano_anterior": True,
    }

def data_range_disponivel() -> dict:
    """Retorna min/max de datas disponíveis no banco — útil para diagnóstico."""
    return query_one("""
        SELECT
            MIN(data_inicio) AS data_minima,
            MAX(data_fim)    AS data_maxima,
            COUNT(*)         AS total_linhas,
            COUNT(DISTINCT campanha) AS total_campanhas
        FROM campanhas_unificada
    """) or {}


# ── Consultas principais ──────────────────────────────────────────────────────


def listar_campanhas(
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> list[dict]:
    """Lista todas as campanhas únicas com período de atividade."""
    where, params = _build_where(
        "campanha IS NOT NULL AND campanha != ''",
        cliente_key=cliente_key,
        plataforma=plataforma,
    )
    return query(f"""
        SELECT
            campanha,
            MIN(data_inicio)                    AS primeira_data,
            MAX(data_fim)                       AS ultima_data,
            COUNT(*)                            AS dias_com_dados,
            ROUND(SUM(investimento), 2)         AS investimento_total
        FROM campanhas_unificada
        {where}
        GROUP BY campanha
        ORDER BY investimento_total DESC
    """, tuple(params))



def verificar_gap_dados(cliente_key: Optional[str] = None, plataforma: Optional[str] = None) -> dict:
    """
    Verifica se existe gap de dados recentes (último dia disponível vs hoje).
    Retorna dict com: ultimo_dia, dias_atrasado, tem_gap, aviso.
    """
    from datetime import date, timedelta
    where, params = _build_where(None, cliente_key=cliente_key, plataforma=plataforma)
    r = query_one(f"""
        SELECT MAX(data_inicio) as ultimo_dia, MAX(data_fim) as ultimo_fim
        FROM campanhas_unificada {where}
    """, tuple(params))

    ultimo = r.get("ultimo_fim") or r.get("ultimo_dia") if r else None
    if not ultimo:
        return {"tem_gap": False, "aviso": None}

    try:
        ultimo_dt = date.fromisoformat(str(ultimo)[:10])
        hoje = date.today()
        ontem = hoje - timedelta(days=1)
        dias_atrasado = (ontem - ultimo_dt).days

        if dias_atrasado <= 0:
            return {"tem_gap": False, "ultimo_dia": str(ultimo_dt), "dias_atrasado": 0, "aviso": None}

        aviso = (
            f"⚠️ _Dados desatualizados: último sync foi {ultimo_dt.strftime('%d/%m')}. "
            f"Mostrando até {ultimo_dt.strftime('%d/%m/%Y')} "
            f"({dias_atrasado} dia{'s' if dias_atrasado > 1 else ''} sem atualização)._"
        )
        return {
            "tem_gap": True,
            "ultimo_dia": str(ultimo_dt),
            "dias_atrasado": dias_atrasado,
            "aviso": aviso,
        }
    except Exception:
        return {"tem_gap": False, "aviso": None}



