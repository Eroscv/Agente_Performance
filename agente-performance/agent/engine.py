"""
agent/engine.py

Motor de consulta do agente.
Recebe uma pergunta em linguagem natural, detecta a intenção,
executa a query correta e retorna os dados estruturados.

Intenções suportadas:
  - listar_campanhas
  - resumo_geral
  - performance_campanha
  - top_roas
  - top_compras
  - evolucao_diaria
  - funil
  - alertas_roas
  - comparar
  - periodo
  - sql_livre  (query SQL direta — uso interno/debug)
"""

import logging
import re
from datetime import date, timedelta
from typing import Any, Optional

from agent import queries
from db.connection import query as raw_query

logger = logging.getLogger(__name__)

# ── Detecção de intenção simples por palavras-chave ───────────────────────────

_INTENT_PATTERNS = [
    ("listar_campanhas",    r"list[ae]|quais\s+campanha|mostra.*campanha|campanhas\s+ativ"),
    ("top_roas",            r"melhor.*roas|maior.*roas|top.*roas|roas.*maior|mais\s+eficiente"),
    ("top_compras",         r"mais\s+compra|maior.*compra|top.*compra|compra.*maior"),
    ("alertas_roas",        r"alerta|roas\s+baixo|roas\s+ruim|abaixo.*roas|problema"),
    ("funil",               r"funil|carrinho|checkout|convers[ãa]o"),
    ("evolucao_diaria",     r"evolu|diário|ao\s+longo|por\s+dia|histórico|tendência"),
    ("comparar",            r"compar|versus|vs\.?|lado\s+a\s+lado"),
    ("performance_campanha",r"performance|desempenho|resultado"),
    ("periodo",             r"período|resumo\s+de|total\s+de|consolidado"),
    ("resumo_geral",        r"resumo|geral|overview|visão\s+geral|como\s+est[áa]"),
]


def _detect_intent(text: str) -> str:
    t = text.lower()
    for intent, pattern in _INTENT_PATTERNS:
        if re.search(pattern, t):
            return intent
    return "resumo_geral"


def _extract_campaign(text: str, available: list[str]) -> Optional[str]:
    """Tenta identificar nome de campanha mencionada no texto."""
    t = text.lower()
    for c in available:
        if c.lower() in t or any(w in t for w in c.lower().split("_") if len(w) > 3):
            return c
    return None


def _extract_dates(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extrai datas do texto.
    Aceita formatos: YYYY-MM-DD, DD/MM/YYYY, ou expressões como
    'últimos 7 dias', 'mês passado', 'esse mês', 'semana passada'.
    """
    today = date.today()

    # Expressões relativas
    if re.search(r"últimos?\s+7\s+dias|semana\s+passada|última\s+semana", text, re.I):
        return (today - timedelta(days=7)).isoformat(), today.isoformat()
    if re.search(r"últimos?\s+30\s+dias|mês\s+passado|último\s+mês", text, re.I):
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    if re.search(r"últimos?\s+90\s+dias|trimestre", text, re.I):
        return (today - timedelta(days=90)).isoformat(), today.isoformat()
    if re.search(r"esse\s+mês|este\s+mês|mês\s+atual", text, re.I):
        inicio = today.replace(day=1)
        return inicio.isoformat(), today.isoformat()

    # Datas explícitas YYYY-MM-DD
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], None

    # Datas DD/MM/YYYY
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


def _get_available_campaigns() -> list[str]:
    rows = raw_query("SELECT DISTINCT campanha FROM campanhas WHERE campanha != '' ORDER BY campanha")
    return [r["campanha"] for r in rows]


# ── Ponto de entrada principal ────────────────────────────────────────────────

def consultar(pergunta: str) -> dict[str, Any]:
    """
    Processa uma pergunta em linguagem natural e retorna:
    {
        "intent":   str,
        "dados":    list[dict] | dict,
        "meta":     dict   # parâmetros usados
    }
    """
    intent             = _detect_intent(pergunta)
    data_inicio, data_fim = _extract_dates(pergunta)
    campanhas_disponiveis = _get_available_campaigns()
    campanha_alvo      = _extract_campaign(pergunta, campanhas_disponiveis)
    limit              = _extract_limit(pergunta)

    logger.info("Intenção: %s | campanha: %s | período: %s → %s",
                intent, campanha_alvo, data_inicio, data_fim)

    meta = {
        "intent":      intent,
        "campanha":    campanha_alvo,
        "data_inicio": data_inicio,
        "data_fim":    data_fim,
        "limit":       limit,
    }

    if intent == "listar_campanhas":
        dados = queries.listar_campanhas()

    elif intent == "resumo_geral":
        if data_inicio and data_fim:
            dados = queries.resumo_periodo(data_inicio, data_fim)
        else:
            dados = queries.resumo_por_campanha(data_inicio, data_fim)

    elif intent == "performance_campanha":
        if campanha_alvo:
            dados = queries.performance_campanha(campanha_alvo, data_inicio, data_fim)
        else:
            dados = queries.resumo_por_campanha(data_inicio, data_fim)

    elif intent == "top_roas":
        dados = queries.top_campanhas_roas(limit, data_inicio, data_fim)

    elif intent == "top_compras":
        dados = queries.top_campanhas_compras(limit, data_inicio, data_fim)

    elif intent == "evolucao_diaria":
        dados = queries.evolucao_diaria(campanha_alvo, data_inicio, data_fim)

    elif intent == "periodo":
        if data_inicio and data_fim:
            dados = queries.resumo_periodo(data_inicio, data_fim)
        else:
            dados = queries.resumo_por_campanha(data_inicio, data_fim)

    elif intent == "funil":
        dados = queries.metricas_funil(campanha_alvo, data_inicio, data_fim)

    elif intent == "alertas_roas":
        dados = queries.alertas_roas_baixo()

    elif intent == "comparar":
        # Tenta extrair múltiplas campanhas do texto
        encontradas = [c for c in campanhas_disponiveis
                       if c.lower() in pergunta.lower()]
        if not encontradas and campanha_alvo:
            encontradas = [campanha_alvo]
        dados = queries.comparar_campanhas(encontradas, data_inicio, data_fim)

    else:
        dados = queries.resumo_por_campanha(data_inicio, data_fim)

    return {"intent": intent, "dados": dados, "meta": meta}


def consultar_sql(sql: str) -> list[dict]:
    """
    Executa SQL diretamente (apenas SELECT).
    Para uso em debug ou consultas avançadas.
    """
    sql_clean = sql.strip()
    if not sql_clean.upper().startswith("SELECT"):
        raise ValueError("Apenas SELECT é permitido via consultar_sql()")
    return raw_query(sql_clean)
