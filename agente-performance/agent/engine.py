"""
agent/engine.py

Motor de consulta do Agente de Performance Guará.
Versão 2 — usa NLP com Claude + fuzzy matching + memória de contexto por thread.

Fluxo:
  1. normalizar_pergunta()       → expande sinônimos
  2. nlp.interpretar()           → Claude extrai intents + campanha + datas
  3. fuzzy_match_campaign()      → tolerância a variações de nome
  4. context_store               → enriquece com histórico da thread
  5. queries.<função>()          → executa SQL no SQLite
  6. resultado consolidado       → retornado ao handler Slack

Compatibilidade: mantém consultar() e consultar_sql() como ponto de entrada.
"""

import logging
from typing import Any, Optional

from agent import queries
from agent.nlp import interpretar, fuzzy_match_campaign
from agent.context_store import get_history, build_context_summary
from db.connection import query as raw_query, get_conn

logger = logging.getLogger(__name__)


# ── helpers legados (mantidos para fallback do nlp.py) ───────────────────────

import re
from datetime import date, timedelta

def _detect_intent(text: str) -> str:
    _INTENT_PATTERNS = [
        ("listar_campanhas",     r"list[ae]|quais\s+campanha|mostra.*campanha|campanhas\s+ativ"),
        ("top_roas",             r"melhor.*roas|maior.*roas|top.*roas|roas.*maior|mais\s+eficiente|vale\s+a\s+pena|t[aá]\s+valendo"),
        ("top_compras",          r"mais\s+compra|maior.*compra|top.*compra|compra.*maior|mais\s+vend"),
        ("alertas_roas",         r"alerta|roas\s+baixo|roas\s+ruim|abaixo.*roas|problema|ruim"),
        ("funil",                r"funil|carrinho|checkout|convers[ãa]o"),
        ("evolucao_diaria",      r"evolu|diário|ao\s+longo|por\s+dia|histórico|tendência"),
        ("comparar",             r"compar|versus|vs\.?|lado\s+a\s+lado"),
        ("performance_campanha", r"performance|desempenho|resultado"),
        ("periodo",              r"período|resumo\s+de|total\s+de|consolidado"),
        ("resumo_geral",         r"resumo|geral|overview|visão\s+geral|como\s+est[áa]"),
    ]
    t = text.lower()
    for intent, pattern in _INTENT_PATTERNS:
        if re.search(pattern, t):
            return intent
    return "resumo_geral"

def _extract_dates(text: str) -> tuple[Optional[str], Optional[str]]:
    today = date.today()
    if re.search(r"últimos?\s+7\s+dias|semana\s+passada|última\s+semana|essa\s+semana|esta\s+semana", text, re.I):
        return (today - timedelta(days=7)).isoformat(), today.isoformat()
    if re.search(r"ontem", text, re.I):
        d = (today - timedelta(days=1)).isoformat()
        return d, d
    if re.search(r"últimos?\s+30\s+dias|mês\s+passado|último\s+mês", text, re.I):
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    if re.search(r"últimos?\s+90\s+dias|trimestre", text, re.I):
        return (today - timedelta(days=90)).isoformat(), today.isoformat()
    if re.search(r"esse\s+mês|este\s+mês|mês\s+atual", text, re.I):
        return today.replace(day=1).isoformat(), today.isoformat()
    # Meses nomeados
    _MONTHS = {
        "janeiro":"01","fevereiro":"02","março":"03","abril":"04",
        "maio":"05","junho":"06","julho":"07","agosto":"08",
        "setembro":"09","outubro":"10","novembro":"11","dezembro":"12",
    }
    for nome, num in _MONTHS.items():
        if re.search(rf"\b{nome}\b", text, re.I):
            ano = "2026" if int(num) <= date.today().month else "2025"
            import calendar
            ultimo = calendar.monthrange(int(ano), int(num))[1]
            return f"{ano}-{num}-01", f"{ano}-{num}-{ultimo:02d}"
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], None
    dates_br = re.findall(r"\d{2}/\d{2}/\d{4}", text)
    if len(dates_br) >= 2:
        def conv(d): return "-".join(reversed(d.split("/")))
        return conv(dates_br[0]), conv(dates_br[1])
    return None, None

def _extract_limit(text: str, default: int = 10) -> int:
    m = re.search(r"top\s*(\d+)|(\d+)\s*(?:maiores|melhores|primeiros)", text, re.I)
    if m:
        return int(m.group(1) or m.group(2))
    return default

def _extract_campaign(text: str, available: list[str]) -> Optional[str]:
    t = text.lower()
    for c in available:
        if c.lower() in t:
            return c
        tokens = [w for w in re.split(r"[_\s/\-]+", c.lower()) if len(w) > 3]
        if any(w in t for w in tokens):
            return c
    return None


# ── helpers internos ──────────────────────────────────────────────────────────

def _get_available_campaigns() -> list[str]:
    rows = raw_query("SELECT DISTINCT campanha FROM campanhas WHERE campanha != '' ORDER BY campanha")
    return [r["campanha"] for r in rows]


def _executar_intent(
    intent:      str,
    campanha:    Optional[str],
    data_inicio: Optional[str],
    data_fim:    Optional[str],
    limit:       int,
) -> Any:
    """Executa uma única intenção e retorna os dados."""

    if intent == "listar_campanhas":
        return queries.listar_campanhas()

    elif intent == "resumo_geral":
        if data_inicio and data_fim:
            return queries.resumo_periodo(data_inicio, data_fim)
        return queries.resumo_por_campanha(data_inicio, data_fim)

    elif intent == "performance_campanha":
        if campanha:
            return queries.performance_campanha(campanha, data_inicio, data_fim)
        return queries.resumo_por_campanha(data_inicio, data_fim)

    elif intent == "top_roas":
        return queries.top_campanhas_roas(limit, data_inicio, data_fim)

    elif intent == "top_compras":
        return queries.top_campanhas_compras(limit, data_inicio, data_fim)

    elif intent == "evolucao_diaria":
        return queries.evolucao_diaria(campanha, data_inicio, data_fim)

    elif intent == "periodo":
        if data_inicio and data_fim:
            return queries.resumo_periodo(data_inicio, data_fim)
        return queries.resumo_por_campanha(data_inicio, data_fim)

    elif intent == "funil":
        return queries.metricas_funil(campanha, data_inicio, data_fim)

    elif intent == "alertas_roas":
        return queries.alertas_roas_baixo()

    elif intent == "comparar":
        campanhas_disponiveis = _get_available_campaigns()
        encontradas = [c for c in campanhas_disponiveis if c.lower() in (campanha or "").lower()]
        if not encontradas and campanha:
            encontradas = [campanha]
        return queries.comparar_campanhas(encontradas, data_inicio, data_fim)

    return queries.resumo_por_campanha(data_inicio, data_fim)


# ── Ponto de entrada principal ────────────────────────────────────────────────

def consultar(
    pergunta:  str,
    thread_ts: Optional[str] = None,
    channel:   Optional[str] = None,
) -> dict[str, Any]:
    """
    Processa uma pergunta em linguagem natural.

    Args:
        pergunta:  texto do usuário
        thread_ts: ID da thread Slack (para memória de contexto)
        channel:   canal Slack (para memória de contexto)

    Returns:
        {
            "intents":     list[str],
            "dados":       dict | list,       # resultado principal
            "dados_extra": dict[str, Any],    # resultados adicionais se múltiplas intenções
            "meta":        dict,
        }
    """
    campanhas_disponiveis = _get_available_campaigns()

    # Histórico da thread para perguntas de acompanhamento
    contexto = build_context_summary(thread_ts) if thread_ts else ""

    # NLP com Claude
    params = interpretar(pergunta, campanhas_disponiveis, contexto)

    intents     = params["intents"]
    campanha    = params["campanha"]
    data_inicio = params["data_inicio"]
    data_fim    = params["data_fim"]
    limit       = params["limit"]

    meta = {
        "intents":     intents,
        "campanha":    campanha,
        "data_inicio": data_inicio,
        "data_fim":    data_fim,
        "limit":       limit,
        "nlp_raw":     params.get("raw", {}),
    }

    # Executa primeira intenção como resultado principal
    dados = _executar_intent(intents[0], campanha, data_inicio, data_fim, limit)

    # Executa intenções adicionais se houver
    dados_extra = {}
    for intent_extra in intents[1:]:
        dados_extra[intent_extra] = _executar_intent(
            intent_extra, campanha, data_inicio, data_fim, limit
        )

    # Compatibilidade retroativa: expõe "intent" (singular)
    meta["intent"] = intents[0]

    return {
        "intent":      intents[0],
        "intents":     intents,
        "dados":       dados,
        "dados_extra": dados_extra,
        "meta":        meta,
    }


def consultar_sql(sql: str) -> list[dict]:
    """Executa SQL direto (apenas SELECT). Para debug/consultas avançadas."""
    sql_clean = sql.strip()
    if not sql_clean.upper().startswith("SELECT"):
        raise ValueError("Apenas SELECT é permitido via consultar_sql()")
    return raw_query(sql_clean)
