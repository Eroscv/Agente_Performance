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
from agent.cache import get as cache_get, set as cache_set
from agent.comparatives import (
    comparar_periodos,
    comparar_campanhas_detalhado,
    comparar_semana_a_semana,
    comparar_mes_a_mes,
    comparar_antes_depois,
    ranking_comparativo,
)
from db.connection import query as raw_query, get_conn

logger = logging.getLogger(__name__)


# ── helpers legados (mantidos para fallback do nlp.py) ───────────────────────

import re
from datetime import date, timedelta

def _detect_intent(text: str) -> str:
    _INTENT_PATTERNS = [
        # Básicas (sem_conversao antes de listar para evitar colisão "quais campanhas sem X")
        ("sem_conversao",           r"sem\s+compra|sem\s+convers|zero\s+compra|gastando.*sem|n.o\s+vend|n.o\s+convert|quais.*sem\s+compra|quais.*n.o\s+conver"),
        ("listar_campanhas",        r"list[ae]|quais\s+campanha|mostra.*campanha|campanhas\s+ativ"),
        # Rankings
        ("top_roas",                r"melhor.*roas|maior.*roas|top.*roas|roas.*maior|mais\s+eficiente|vale\s+a\s+pena|t[aá]\s+valendo"),
        ("top_compras",             r"mais\s+compra|maior.*compra|top.*compra|compra.*maior|mais\s+vend"),
        ("top_receita",             r"mais\s+receita|maior.*receita|top.*receita|mais.*faturou|maior.*faturamento"),
        ("top_ctr",                 r"melhor.*ctr|maior.*ctr|top.*ctr|clique.*eficiente|mais\s+clique"),
        ("top_cpm",                 r"mais\s+barato.*impres|menor.*cpm|top.*cpm|cpm.*menor|custo.*impressão"),
        ("top_alcance",             r"mais.*alcance|maior.*alcance|top.*alcance|mais.*pessoas"),
        ("top_cliques",             r"mais\s+cliques|maior.*cliques|top.*cliques|volume.*cliques|cliques.*link"),
        # Análises específicas
        ("alertas_roas",            r"alerta|roas\s+baixo|roas\s+ruim|abaixo.*roas|problema|ruim"),
        ("campanhas_inativas",      r"inativ|pausad|parou|n.o\s+rod|encerrad"),
        ("variacao_roas",           r"volatilidade|variação.*roas|roas.*variou|oscilação|pico.*roas"),
        ("saturacao_frequencia",    r"satura|frequência\s+alta|muitas\s+vezes|cansa|repetição|audiência.*cansad"),
        ("eficiencia_criativo",     r"criativo|hook\s+rate|retenção.*vídeo|qualidade.*anúncio|scroll\s+stop"),
        ("distribuicao_investimento", r"distribui|share\s+budget|alocação|onde.*dinheiro|budget.*campanhas"),
        ("historico_mensal",        r"histór|mês.*a.*mês|mensal|sazonalidade|ao\s+longo\s+dos\s+meses"),
        ("funil_detalhado",         r"funil\s+detalhado|custo\s+por\s+etapa|onde\s+vaza|perda\s+no\s+funil"),
        ("funil",                   r"funil|carrinho|checkout|convers[ãa]o"),
        ("metricas_video",          r"vídeo|video|thruplay|play|reprodução|retenção\s+vídeo|view.*video"),
        ("metricas_engajamento",    r"engajamento|curtida|comentário|compartilhamento|salvamento|reação"),
        ("metricas_mensageria",     r"mensage|conversa|whatsapp|messenger|lead.*mensagem"),
        ("resumo_executivo",        r"executivo|visão\s+completa|panorama|tudo\s+sobre|dashboard"),
        ("evolucao_diaria",         r"evolu|diário|ao\s+longo|por\s+dia|histórico|tendência"),
        ("comparar",                r"compar|versus|vs\.?|lado\s+a\s+lado"),
        ("performance_campanha",    r"performance|desempenho|resultado"),
        ("periodo",                 r"período|resumo\s+de|total\s+de|consolidado"),
        ("resumo_geral",            r"resumo|geral|overview|visão\s+geral|como\s+est[áa]"),
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

def _validar_campanha(campanha: Optional[str], disponiveis: list[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Valida se a campanha existe. Retorna (campanha_valida, mensagem_erro).
    mensagem_erro é None se tudo OK.
    """
    if not campanha:
        return None, None
    if campanha in disponiveis:
        return campanha, None
    # Campanha não está na lista — fuzzy matching já falhou
    sugestoes = disponiveis[:5]
    lista = "\n".join(f"  • {c}" for c in sugestoes)
    msg = (
        f"⚠️ Não encontrei a campanha *{campanha}* no banco de dados.\n\n"
        f"Campanhas disponíveis (principais):\n{lista}\n\n"
        f"_Digite `/campanhas` para ver a lista completa._"
    )
    return None, msg


def _dados_vazios_sugestao(intent: str, campanha: Optional[str], data_inicio: Optional[str], data_fim: Optional[str]) -> Optional[str]:
    """
    Quando uma query retorna vazio, sugere o período mais recente disponível com dados.
    Retorna mensagem de sugestão ou None se houver dados.
    """
    try:
        from agent.queries import data_range_disponivel
        dr = data_range_disponivel()
        db_min = dr.get("data_minima")
        db_max = dr.get("data_maxima")

        if not db_max:
            return None

        # Verifica se há dados sem filtro de período
        if data_inicio or data_fim:
            sem_filtro = raw_query(
                "SELECT MIN(data_inicio) AS ini, MAX(data_fim) AS fim, COUNT(*) AS n "
                "FROM campanhas WHERE campanha IS NOT NULL AND campanha != ''"
                + (f" AND campanha = '{campanha}'" if campanha else "")
            )
            if sem_filtro and sem_filtro[0].get("n", 0) > 0:
                row = sem_filtro[0]
                # Sugere período mais recente com mesma duração
                sugestao_periodo = ""
                if data_inicio and data_fim:
                    from datetime import date as _date, timedelta as _td
                    try:
                        di = _date.fromisoformat(data_inicio)
                        df = _date.fromisoformat(data_fim)
                        duracao = (df - di).days
                        novo_df = _date.fromisoformat(db_max)
                        novo_di = novo_df - _td(days=duracao)
                        sugestao_periodo = f" Sugestão: tente *{novo_di.isoformat()} → {db_max}*."
                    except Exception:
                        pass
                return (
                    f"⚠️ Nenhum dado encontrado para o período *{data_inicio} → {data_fim}*."
                    + (f" (campanha: *{campanha}*)" if campanha else "")
                    + f"\n\n📅 Dados disponíveis de *{row['ini']}* a *{row['fim']}* "
                    f"({row['n']} registros).{sugestao_periodo}"
                )
        return None
    except Exception:
        return None


def _get_available_campaigns() -> list[str]:
    """Retorna campanhas ordenadas por investimento total (mais relevantes primeiro)."""
    rows = raw_query("""
        SELECT campanha FROM campanhas
        WHERE campanha IS NOT NULL AND campanha != ''
        GROUP BY campanha
        ORDER BY SUM(investimento) DESC
    """)
    return [r["campanha"] for r in rows]


def _executar_intent(
    intent:      str,
    campanha:    Optional[str],
    data_inicio: Optional[str],
    data_fim:    Optional[str],
    limit:       int,
    meta_extra:  dict = None,
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
        # Usuário pediu performance de campanha específica mas não foi possível identificar qual
        # Retornar lista das principais para orientar o usuário
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

    elif intent == "historico_mensal":
        return queries.historico_mensal(campanha)

    elif intent == "resumo_executivo":
        return queries.resumo_executivo(data_inicio, data_fim)

    elif intent == "top_receita":
        return queries.top_campanhas_receita(limit, data_inicio, data_fim)

    elif intent == "top_ctr":
        return queries.top_campanhas_ctr(limit, data_inicio, data_fim)

    elif intent == "top_cpm":
        return queries.top_campanhas_cpm(limit, data_inicio, data_fim)

    elif intent == "top_alcance":
        return queries.top_campanhas_alcance(limit, data_inicio, data_fim)

    elif intent == "top_cliques":
        return queries.top_campanhas_cliques(limit, data_inicio, data_fim)

    elif intent == "funil_detalhado":
        return queries.custo_funil_detalhado(campanha, data_inicio, data_fim)

    elif intent == "eficiencia_criativo":
        return queries.eficiencia_criativo(campanha, data_inicio, data_fim)

    elif intent == "distribuicao_investimento":
        return queries.distribuicao_investimento(data_inicio, data_fim)

    elif intent == "sem_conversao":
        return queries.campanhas_sem_conversao(data_inicio, data_fim)

    elif intent == "campanhas_inativas":
        return queries.campanhas_inativas()

    elif intent == "variacao_roas":
        return queries.variacao_diaria_roas(campanha, data_inicio, data_fim)

    elif intent == "saturacao_frequencia":
        return queries.saturacao_frequencia(data_inicio, data_fim)

    elif intent == "metricas_video":
        return queries.metricas_video(campanha, data_inicio, data_fim)

    elif intent == "metricas_engajamento":
        return queries.metricas_engajamento(campanha, data_inicio, data_fim)

    elif intent == "metricas_mensageria":
        return queries.metricas_mensageria(campanha, data_inicio, data_fim)

    elif intent == "comparar":
        campanhas_todas = _get_available_campaigns()
        encontradas = [c for c in campanhas_todas if c.lower() in (campanha or "").lower()]
        if not encontradas and campanha:
            encontradas = [campanha]
        return comparar_campanhas_detalhado(encontradas, data_inicio, data_fim)

    elif intent == "comparar_periodos":
        nlp_raw     = meta_extra.get("nlp_raw", {}) if meta_extra else {}
        ini_a = nlp_raw.get("periodo_a_inicio") or data_inicio
        fim_a = nlp_raw.get("periodo_a_fim")    or data_fim
        ini_b = nlp_raw.get("periodo_b_inicio")
        fim_b = nlp_raw.get("periodo_b_fim")
        if ini_a and fim_a and ini_b and fim_b:
            return comparar_periodos(ini_a, fim_a, ini_b, fim_b, campanha)
        return comparar_mes_a_mes(campanha)

    elif intent == "comparar_wow":
        return comparar_semana_a_semana(campanha)

    elif intent == "comparar_mom":
        return comparar_mes_a_mes(campanha)

    elif intent == "comparar_antes_depois":
        nlp_raw    = meta_extra.get("nlp_raw", {}) if meta_extra else {}
        data_virada = nlp_raw.get("data_virada") or data_inicio
        if data_virada:
            return comparar_antes_depois(data_virada, campanha)
        return queries.resumo_por_campanha(data_inicio, data_fim)

    elif intent == "ranking_metrica":
        nlp_raw = meta_extra.get("nlp_raw", {}) if meta_extra else {}
        metrica = nlp_raw.get("metrica") or "roas"
        return ranking_comparativo(metrica, data_inicio, data_fim)

    return queries.resumo_por_campanha(data_inicio, data_fim)


# ── Ponto de entrada principal ────────────────────────────────────────────────

def consultar(
    pergunta:  str,
    thread_ts: Optional[str] = None,
    channel:   Optional[str] = None,
    user_id:   Optional[str] = None,
) -> dict[str, Any]:
    """
    Processa uma pergunta em linguagem natural.
    Usa cache, log, validação de campanha e sugestão de vazio.
    """
    import time
    t_inicio = time.time()

    # 1. Verifica cache
    cached = cache_get(pergunta)
    if cached:
        return cached

    campanhas_disponiveis = _get_available_campaigns()
    contexto = build_context_summary(thread_ts) if thread_ts else ""

    # 2. NLP com Claude
    params = interpretar(pergunta, campanhas_disponiveis, contexto)

    intents     = params["intents"]
    campanha    = params["campanha"]
    data_inicio = params["data_inicio"]
    data_fim    = params["data_fim"]
    limit       = params["limit"]
    campanha_raw = params.get("raw", {}).get("campanha")  # nome original antes do fuzzy
    periodo_nota = params.get("periodo_nota")  # aviso de ajuste automático de período

    # 3. Valida campanha
    # Se NLP extraiu um nome de campanha (raw) mas fuzzy não encontrou match,
    # retorna erro com sugestões em vez de silenciosamente mostrar resumo geral
    if campanha is None and campanha_raw and "performance" in " ".join(intents):
        sugestoes = campanhas_disponiveis[:8]
        lista = "\n".join(f"  • {c}" for c in sugestoes)
        erro_campanha = (
            f"⚠️ Não encontrei nenhuma campanha com o nome *{campanha_raw}*.\n\n"
            f"*Campanhas disponíveis (por investimento):*\n{lista}\n"
            f"...e mais {max(0, len(campanhas_disponiveis) - 8)} outras.\n\n"
            f"_Tente usar parte do nome, ex: `performance Advantage` ou `performance ABO`_"
        )
        resultado = {
            "intent":      intents[0],
            "intents":     intents,
            "dados":       [],
            "dados_extra": {},
            "meta":        {"intent": intents[0], "intents": intents,
                            "campanha": campanha_raw, "data_inicio": data_inicio,
                            "data_fim": data_fim, "limit": limit},
            "erro":        erro_campanha,
        }
        return resultado

    campanha_valida, erro_campanha = _validar_campanha(campanha, campanhas_disponiveis)
    if erro_campanha:
        resultado = {
            "intent":       intents[0],
            "intents":      intents,
            "dados":        [],
            "dados_extra":  {},
            "meta":         {"intent": intents[0], "intents": intents,
                             "campanha": campanha, "data_inicio": data_inicio,
                             "data_fim": data_fim, "limit": limit},
            "erro":         erro_campanha,
        }
        return resultado

    campanha = campanha_valida
    meta = {
        "intents":      intents,
        "campanha":     campanha,
        "data_inicio":  data_inicio,
        "data_fim":     data_fim,
        "limit":        limit,
        "nlp_raw":      params.get("raw", {}),
        "intent":       intents[0],
        "periodo_nota": periodo_nota,  # ← propagado para o claude_client via meta
    }

    # 4. Executa queries
    dados = _executar_intent(intents[0], campanha, data_inicio, data_fim, limit, meta)

    dados_extra = {}
    for intent_extra in intents[1:]:
        dados_extra[intent_extra] = _executar_intent(
            intent_extra, campanha, data_inicio, data_fim, limit, meta
        )

    # 5. Verifica dados vazios e sugere alternativa
    dados_vazios_msg = None
    dados_principais_vazios = (
        (isinstance(dados, list) and len(dados) == 0) or
        (isinstance(dados, dict) and not dados)
    )
    if dados_principais_vazios:
        dados_vazios_msg = _dados_vazios_sugestao(intents[0], campanha, data_inicio, data_fim)

    resultado = {
        "intent":          intents[0],
        "intents":         intents,
        "dados":           dados,
        "dados_extra":     dados_extra,
        "meta":            meta,
        "aviso_vazio":     dados_vazios_msg,
    }

    # 6. Salva no cache (não cacheia resultados vazios)
    if not dados_principais_vazios:
        cache_set(pergunta, resultado)

    # Log centralizado no handler Slack (tem resposta completa + latência ponta-a-ponta)
    return resultado


def consultar_sql(sql: str) -> list[dict]:
    """Executa SQL direto (apenas SELECT). Para debug/consultas avançadas."""
    sql_clean = sql.strip()
    if not sql_clean.upper().startswith("SELECT"):
        raise ValueError("Apenas SELECT é permitido via consultar_sql()")
    return raw_query(sql_clean)
