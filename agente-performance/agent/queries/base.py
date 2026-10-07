"""
agent/queries/base.py

Helpers internos, diagnóstico e consultas base.
"""
import logging
from typing import Optional

from db.connections import query_fed as query, query_fed_one as query_one, LEGACY_DB_PATH as DB_PATH
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
        hoje = hoje_brt()
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


# ── Guardrails de qualidade de dados ────────────────────────────────────────

# Volume mínimo esperado de linhas por dia (baseline dos últimos 30 dias).
# Se o dia tiver menos de VOLUME_MIN_PCT do baseline, dado é considerado parcial.
_VOLUME_MIN_PCT = 0.40   # menos de 40% do volume médio = parcial
_VOLUME_DROP_PCT = 0.20  # investimento < 20% do médio dos 7 dias anteriores = dado parcial ou anomalia

# Janela de atribuição por plataforma (dias em que os dados ainda podem ser revisados pela API)
_JANELA_ATRIBUICAO = {
    "meta_ads":   3,   # Meta revisa compras em até 7d; dados das últimas 3d costumam ainda mudar
    "google_ads": 2,   # Google finaliza em geral em 2d
    "shopify":    0,   # Shopify é transacional, não tem janela
}


def validar_cobertura_dados(
    data: str,
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> dict:
    """
    Guardrail 1+2: valida se os dados de `data` estão completos.

    Verifica:
      - Volume de linhas vs baseline dos últimos 30 dias (dado parcial)
      - Queda brusca de investimento vs média dos 7 dias anteriores (dado suspeito)

    Retorna:
        {
            "ok":      bool,          # False = dado provavelmente parcial
            "motivo":  str | None,    # descrição do problema detectado
            "aviso":   str | None,    # mensagem pronta para Slack
            "linhas":  int,           # linhas encontradas para `data`
            "invest":  float,         # investimento encontrado para `data`
        }
    """
    from datetime import date as _date, timedelta as _td

    try:
        dt = _date.fromisoformat(data)
        d7_ini = (dt - _td(days=8)).isoformat()   # 7 dias antes (exclui o próprio dia)
        d7_fim = (dt - _td(days=1)).isoformat()
        d30_ini = (dt - _td(days=31)).isoformat()
        d30_fim = (dt - _td(days=1)).isoformat()

        # Filtra por cliente/plataforma se fornecido
        plat_filter = ""
        params_base: list = []
        if cliente_key:
            plat_filter += " AND cliente_key = ?"
            params_base.append(cliente_key)
        if plataforma:
            plat_filter += " AND plataforma = ?"
            params_base.append(plataforma)

        # Linhas e investimento do dia alvo
        r_dia = query_one(
            f"SELECT COUNT(*) linhas, COALESCE(SUM(investimento),0) invest "
            f"FROM campanhas_unificada "
            f"WHERE data_inicio = ?{plat_filter}",
            tuple([data] + params_base),
        )
        linhas_dia  = r_dia["linhas"]  if r_dia else 0
        invest_dia  = r_dia["invest"]  if r_dia else 0.0

        if linhas_dia == 0:
            return {
                "ok": False, "motivo": "sem_dados",
                "aviso": None,  # tratado pelo fluxo sem_dados existente
                "linhas": 0, "invest": 0.0,
            }

        # Baseline 30 dias: média de linhas/dia
        r_30 = query_one(
            f"SELECT COUNT(*) total_linhas, COUNT(DISTINCT data_inicio) total_dias "
            f"FROM campanhas_unificada "
            f"WHERE data_inicio BETWEEN ? AND ?{plat_filter}",
            tuple([d30_ini, d30_fim] + params_base),
        )
        total_linhas_30 = r_30["total_linhas"] if r_30 else 0
        total_dias_30   = r_30["total_dias"]   if r_30 else 0
        media_linhas_30 = total_linhas_30 / total_dias_30 if total_dias_30 > 0 else 0

        # Média de investimento nos 7 dias anteriores
        r_7 = query_one(
            f"SELECT COALESCE(AVG(invest_dia),0) media_invest "
            f"FROM ("
            f"  SELECT data_inicio, SUM(investimento) invest_dia "
            f"  FROM campanhas_unificada "
            f"  WHERE data_inicio BETWEEN ? AND ?{plat_filter} "
            f"  GROUP BY data_inicio"
            f") sub",
            tuple([d7_ini, d7_fim] + params_base),
        )
        media_invest_7 = r_7["media_invest"] if r_7 else 0.0

        avisos = []

        # Guardrail 1: volume de linhas abaixo do mínimo
        if media_linhas_30 > 5 and linhas_dia < media_linhas_30 * _VOLUME_MIN_PCT:
            pct = linhas_dia / media_linhas_30 * 100
            avisos.append(
                f"volume de campanhas muito baixo ({linhas_dia} linhas vs média {media_linhas_30:.0f}/dia — {pct:.0f}% do normal)"
            )

        # Guardrail 2: queda brusca de investimento
        if media_invest_7 > 100 and invest_dia < media_invest_7 * _VOLUME_DROP_PCT:
            pct = invest_dia / media_invest_7 * 100
            avisos.append(
                f"investimento suspeito: R$ {invest_dia:,.0f} vs média R$ {media_invest_7:,.0f} ({pct:.0f}% do normal)"
            )

        if not avisos:
            return {"ok": True, "motivo": None, "aviso": None, "linhas": linhas_dia, "invest": invest_dia}

        plat_label = plataforma or "todas as plataformas"
        cli_label  = cliente_key or "todos os clientes"
        motivo     = " | ".join(avisos)
        aviso_txt  = (
            f"⚠️ *Dado possivelmente parcial — {cli_label} / {plat_label} / {data}*\n"
            f"_{motivo}_\n"
            f"_Os dados podem ainda estar sendo consolidados pela API. "
            f"O relatório será regenerado automaticamente no próximo ciclo._"
        )
        return {"ok": False, "motivo": motivo, "aviso": aviso_txt, "linhas": linhas_dia, "invest": invest_dia}

    except Exception as e:
        logger.debug("[validar_cobertura_dados] erro: %s", e)
        return {"ok": True, "motivo": None, "aviso": None, "linhas": -1, "invest": -1.0}  # fail open


def aviso_janela_atribuicao(
    data_fim: str,
    plataforma: Optional[str] = None,
) -> Optional[str]:
    """
    Guardrail 3: avisa quando o período solicitado está dentro da janela de
    atribuição da plataforma (dados ainda podem mudar).

    Retorna string de aviso ou None se o período já está consolidado.
    """
    from datetime import date as _date
    try:
        dt_fim = _date.fromisoformat(data_fim)
        hoje   = _hoje_brt()
        dias_atras = (hoje - dt_fim).days

        plats = [plataforma] if plataforma else list(_JANELA_ATRIBUICAO.keys())
        avisos = []
        for plat in plats:
            janela = _JANELA_ATRIBUICAO.get(plat, 0)
            if janela > 0 and dias_atras < janela:
                plat_label = {"meta_ads": "Meta Ads", "google_ads": "Google Ads"}.get(plat, plat)
                avisos.append(
                    f"{plat_label} (janela de {janela}d — dados até {dt_fim.strftime('%d/%m')} ainda podem ser revisados pela API)"
                )

        if not avisos:
            return None

        return (
            f"⏳ *Atenção: período dentro da janela de atribuição*\n"
            f"_{'; '.join(avisos)}_\n"
            f"_Os números de compras/conversões podem aumentar nas próximas {max(_JANELA_ATRIBUICAO[p] for p in plats if p in _JANELA_ATRIBUICAO)} horas._"
        )
    except Exception as e:
        logger.debug("[aviso_janela_atribuicao] erro: %s", e)
        return None

