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

# ── Segmentos / clientes ──────────────────────────────────────────────────────

import json as _json
import os as _os

_CLIENTES_JSON = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "clientes.json")

# Labels amigáveis para exibição
_PLATAFORMA_LABEL = {
    "meta_ads":   "Meta Ads",
    "google_ads": "Google Ads",
}


def _carregar_segmentos_ativos() -> list[dict]:
    """
    Lê clientes.json e retorna lista de segmentos ativos.

    Cada item:
        {
          "cliente_key":  str,   # ex: "source_of"
          "plataforma":   str,   # ex: "meta_ads"
          "nome_cliente": str,   # ex: "Source OF"
          "nome_plat":    str,   # ex: "Meta Ads"
          "emoji":        str,   # ex: "🔵"
          "label":        str,   # ex: "Source OF · Meta Ads"
        }

    Exclui shopify (não é plataforma de anúncios) e entradas com ativo=false.
    Resultado é cacheado em memória (arquivo raramente muda).
    """
    try:
        with open(_CLIENTES_JSON, encoding="utf-8") as f:
            dados = _json.load(f)
    except Exception as e:
        logger.error("[segmentos] Falha ao ler clientes.json: %s", e)
        return []

    segmentos = []
    for ck, cfg in dados.get("clientes", {}).items():
        if not cfg.get("ativo", False):
            continue
        nome_cliente = cfg.get("nome", ck)
        emoji        = cfg.get("slack_emoji", "📊")
        for plat, pcfg in cfg.get("plataformas", {}).items():
            if plat == "shopify":
                continue
            if not pcfg.get("ativo", False):
                continue
            nome_plat = _PLATAFORMA_LABEL.get(plat, plat)
            segmentos.append({
                "cliente_key":  ck,
                "plataforma":   plat,
                "nome_cliente": nome_cliente,
                "nome_plat":    nome_plat,
                "emoji":        emoji,
                "label":        f"{nome_cliente} · {nome_plat}",
            })
    return segmentos


def get_contexto_cliente(canal_id: str) -> Optional[str]:
    """
    Infere cliente_key a partir do nome/id do canal Slack.

    Regras (case-insensitive):
      - canal contém 'pod' ou 'kombucha' → 'pod_kombucha'
      - canal contém 'source' ou 'sof'   → 'source_of'
      - senão                            → None (canal genérico)
    """
    if not canal_id:
        return None
    c = canal_id.lower()
    if "pod" in c or "kombucha" in c:
        return "pod_kombucha"
    if "source" in c or "sof" in c:
        return "source_of"
    return None


# ── helpers legados (mantidos para fallback do nlp.py) ───────────────────────

import re
from datetime import date, timedelta

def _detect_intent(text: str) -> str:
    import unicodedata as _ud
    # Normaliza acentos para que "frequencia" == "frequência", "nao" == "não"
    def _norm(s: str) -> str:
        return _ud.normalize("NFD", s).encode("ascii", "ignore").decode("ascii").lower()
    text_norm = _norm(text)

    _INTENT_PATTERNS = [
        # Básicas (sem_conversao antes de listar para evitar colisão "quais campanhas sem X")
        ("sem_conversao",           r"sem\s+compra|sem\s+convers|zero\s+compra|gastando.*sem|nao.*vend|nao.*conver|nao.*est..*conver|sem\s+retorno|desperd|jogando.*dinheiro|dinheiro.*fora|quais.*sem\s+compra"),
        ("listar_campanhas",        r"list[ae]|quais\s+campanha|mostra.*campanha|campanhas\s+ativ"),
        # Rankings
        ("top_roas",                r"melhor.*roas|maior.*roas|top.*roas|roas.*maior|mais\s+eficiente|vale\s+a\s+pena|t[aá]\s+valendo"),
        ("top_compras",             r"mais\s+compra|maior.*compra|top.*compra|compra.*maior|mais\s+vend"),
        ("top_receita",             r"mais\s+receita|maior.*receita|top.*receita|mais.*faturou|maior.*faturamento"),
        ("top_cliques",             r"mais\s+cliques?|maior.*cliques?|top.*cliques?|volume.*cliques?|cliques?.*link|qual.*mais\s+cliques?"),
        ("top_ctr",                 r"melhor.*ctr|maior.*ctr|top.*ctr|clique.*eficiente|taxa\s+de\s+clique|maior\s+taxa\s+de\s+clique"),
        ("top_cpm",                 r"mais\s+barato.*impres|menor.*cpm|top.*cpm|cpm.*menor|custo.*impress"),
        ("top_alcance",             r"mais.*alcance|maior.*alcance|top.*alcance|mais.*pessoas|maior.*público"),
        # Análises específicas
        ("alertas_roas",            r"alerta|roas\s+baixo|roas\s+ruim|abaixo.*roas|ruim|ruins|pior.*roas|roas.*abaixo|campanha.*ruins?|mal.*performan|performan.*ruins?|roas\s+de\s+[0-9]|abaixo\s+de|abaixo.*esperado|esperado.*abaixo|problema.*campanha"),
        ("campanhas_inativas",      r"inativ|pausad|parou|n.o\s+rod|encerrad"),
        ("variacao_roas",           r"volatilidade|variacao.*roas|variação.*roas|roas.*variou|oscilacao|oscilação|pico.*roas|instabilidade.*roas"),
        ("saturacao_frequencia",    r"satura|frequen.+alta|muitas\s+vezes|cansa|repeti|audiencia.*cansad|freq.*alta"),
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
        ("projecao_mes",            r"projecao|projeção|fechamento.*mes|ritmo.*mes|se continuar|vai fechar|estimativa.*mes|quanto.*fechar"),
        ("sazonalidade",            r"ano anterior|ano passado|mesmo periodo.*ano|black friday|dia das maes|natal|sazonalidade|comparar.*ano"),
        ("top_criativos",           r"criativo|anuncio|ad\b|melhor.*anuncio|pior.*anuncio|top.*criativo|qual.*anuncio"),
        ("criativos_fadiga",        r"fadiga|saturad|frequencia.*alta|cansado|repetido|renovar.*criativo|pausar.*criativo"),
        ("periodo",                 r"período|resumo\s+de|total\s+de|consolidado"),
        ("resumo_geral",            r"resumo|geral|overview|visão\s+geral|como\s+est[áa]"),
    ]
    for intent, pattern in _INTENT_PATTERNS:
        if re.search(pattern, text_norm, re.I):
            return intent
    return "resumo_geral"

def _extract_dates(text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extrai datas do texto via PERIOD_SYNONYMS dinâmico do synonyms.py.
    Fallback para regex de datas explícitas (YYYY-MM-DD, DD/MM/YYYY).
    """
    from agent.synonyms import PERIOD_SYNONYMS

    # Datas ISO explícitas têm prioridade máxima (evita sinônimos como "2026" engolindo datas)
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        # Verifica se há segunda data no formato BR também
        pass  # continua para BR abaixo

    # Datas BR (DD/MM/YYYY)
    dates_br = re.findall(r"\d{2}/\d{2}/\d{4}", text)
    if len(dates_br) >= 2:
        def conv(d): return "-".join(reversed(d.split("/")))
        return conv(dates_br[0]), conv(dates_br[1])

    if len(dates) == 1:
        return dates[0], None

    # Sinônimos de período (mais específico primeiro — chaves mais longas)
    t_lower = text.lower()
    for sinonimo in sorted(PERIOD_SYNONYMS.keys(), key=len, reverse=True):
        if sinonimo in t_lower:
            periodo = PERIOD_SYNONYMS[sinonimo]
            # formato: "YYYY-MM-DD a YYYY-MM-DD"
            partes = periodo.split(" a ")
            if len(partes) == 2:
                return partes[0].strip(), partes[1].strip()

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
                "SELECT MIN(data) AS ini, MAX(data) AS fim, COUNT(*) AS n "
                "FROM v_performance WHERE campanha IS NOT NULL AND campanha != ''"
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
        SELECT campanha FROM v_performance
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
    cliente_key: Optional[str] = None,
    plataforma:  Optional[str] = None,
) -> Any:
    """
    Executa uma única intenção e retorna os dados.

    cliente_key / plataforma são opcionais e repassados a todas as queries
    que os aceitam, filtrando os dados por segmento.
    """
    # kwargs comuns de filtro (só passados quando não-None)
    _fk: dict = {}
    if cliente_key:
        _fk["cliente_key"] = cliente_key
    if plataforma:
        _fk["plataforma"] = plataforma

    if intent == "listar_campanhas":
        return queries.listar_campanhas(**_fk)

    elif intent == "resumo_geral":
        if data_inicio and data_fim:
            return queries.resumo_periodo(data_inicio, data_fim, **_fk)
        return queries.resumo_por_campanha(data_inicio, data_fim, **_fk)

    elif intent == "performance_campanha":
        if campanha:
            return queries.performance_campanha(campanha, data_inicio, data_fim, **_fk)
        return queries.resumo_por_campanha(data_inicio, data_fim, **_fk)

    elif intent == "top_roas":
        return queries.top_campanhas_roas(limit, data_inicio, data_fim, **_fk)

    elif intent == "top_compras":
        return queries.top_campanhas_compras(limit, data_inicio, data_fim, **_fk)

    elif intent == "evolucao_diaria":
        return queries.evolucao_diaria(campanha, data_inicio, data_fim, **_fk)

    elif intent == "periodo":
        if data_inicio and data_fim:
            return queries.resumo_periodo(data_inicio, data_fim, **_fk)
        return queries.resumo_por_campanha(data_inicio, data_fim, **_fk)

    elif intent == "funil":
        return queries.metricas_funil(campanha, data_inicio, data_fim, **_fk)

    elif intent == "alertas_roas":
        return queries.alertas_roas_baixo(**_fk)

    elif intent == "historico_mensal":
        return queries.historico_mensal(campanha, **_fk)

    elif intent == "resumo_executivo":
        return queries.resumo_executivo(data_inicio, data_fim, **_fk)

    elif intent == "top_receita":
        return queries.top_campanhas_receita(limit, data_inicio, data_fim, **_fk)

    elif intent == "top_ctr":
        return queries.top_campanhas_ctr(limit, data_inicio, data_fim, **_fk)

    elif intent == "top_cpm":
        return queries.top_campanhas_cpm(limit, data_inicio, data_fim, **_fk)

    elif intent == "top_alcance":
        return queries.top_campanhas_alcance(limit, data_inicio, data_fim, **_fk)

    elif intent == "top_cliques":
        return queries.top_campanhas_cliques(limit, data_inicio, data_fim, **_fk)

    elif intent == "funil_detalhado":
        return queries.custo_funil_detalhado(campanha, data_inicio, data_fim, **_fk)

    elif intent == "eficiencia_criativo":
        return queries.eficiencia_criativo(campanha, data_inicio, data_fim, **_fk)

    elif intent == "distribuicao_investimento":
        return queries.distribuicao_investimento(data_inicio, data_fim, **_fk)

    elif intent == "sem_conversao":
        return queries.campanhas_sem_conversao(data_inicio, data_fim, **_fk)

    elif intent == "campanhas_inativas":
        return queries.campanhas_inativas(**_fk)

    elif intent == "variacao_roas":
        return queries.variacao_diaria_roas(campanha, data_inicio, data_fim, **_fk)

    elif intent == "saturacao_frequencia":
        return queries.saturacao_frequencia(data_inicio, data_fim, **_fk)

    elif intent == "metricas_video":
        return queries.metricas_video(campanha, data_inicio, data_fim, **_fk)

    elif intent == "metricas_engajamento":
        return queries.metricas_engajamento(campanha, data_inicio, data_fim, **_fk)

    elif intent == "metricas_mensageria":
        return queries.metricas_mensageria(campanha, data_inicio, data_fim, **_fk)

    elif intent == "top_criativos":
        return queries.top_criativos_roas(campanha, data_inicio, data_fim, limit, **_fk)

    elif intent == "criativos_fadiga":
        return queries.criativos_em_fadiga(data_inicio=data_inicio, data_fim=data_fim, **_fk)

    elif intent == "projecao_mes":
        return queries.projecao_fechamento_mes(**_fk)

    elif intent == "sazonalidade":
        return queries.comparar_mesmo_periodo_ano_anterior(data_inicio, data_fim, **_fk)

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
        return queries.resumo_por_campanha(data_inicio, data_fim, **_fk)

    elif intent == "ranking_metrica":
        nlp_raw = meta_extra.get("nlp_raw", {}) if meta_extra else {}
        metrica = nlp_raw.get("metrica") or "roas"
        return ranking_comparativo(metrica, data_inicio, data_fim)

    return queries.resumo_por_campanha(data_inicio, data_fim, **_fk)


# ── Execução por segmento (multi-cliente) ─────────────────────────────────────

def executar_por_segmento(
    intent:      str,
    params:      dict,
    meta_extra:  dict = None,
) -> list[dict]:
    """
    Executa a intent separadamente para cada segmento ativo (cliente + plataforma).

    Usado quando o NLP não detectou cliente/plataforma e o canal não tem contexto
    de cliente definido — em vez de misturar dados de todos os clientes, retorna
    seções distintas identificadas por cliente + plataforma.

    Retorna lista de dicts:
        [
          {
            "cliente_key":  "source_of",
            "plataforma":   "meta_ads",
            "nome_cliente": "Source OF",
            "nome_plat":    "Meta Ads",
            "emoji":        "🔵",
            "label":        "Source OF · Meta Ads",
            "dados":        <resultado da query>,
            "vazio":        bool,
          },
          ...
        ]
    """
    segmentos = _carregar_segmentos_ativos()
    if not segmentos:
        logger.warning("[segmento] Nenhum segmento ativo encontrado em clientes.json")
        return []

    campanha    = params.get("campanha")
    data_inicio = params.get("data_inicio")
    data_fim    = params.get("data_fim")
    limit       = params.get("limit", 10)

    resultados = []
    for seg in segmentos:
        ck   = seg["cliente_key"]
        plat = seg["plataforma"]
        try:
            dados = _executar_intent(
                intent,
                campanha,
                data_inicio,
                data_fim,
                limit,
                meta_extra,
                cliente_key=ck,
                plataforma=plat,
            )
            vazio = (
                (isinstance(dados, list) and len(dados) == 0) or
                (isinstance(dados, dict) and not dados) or
                dados is None
            )
            resultados.append({
                **seg,
                "dados": dados,
                "vazio": vazio,
            })
            logger.debug(
                "[segmento] %s | intent=%s | vazio=%s",
                seg["label"], intent, vazio,
            )
        except Exception as e:
            logger.error("[segmento] Erro em %s: %s", seg["label"], e)
            resultados.append({
                **seg,
                "dados": None,
                "vazio": True,
                "erro": str(e),
            })

    return resultados


# ── Ponto de entrada principal ────────────────────────────────────────────────

def consultar(
    pergunta:  str,
    thread_ts: Optional[str] = None,
    channel:   Optional[str] = None,
    user_id:   Optional[str] = None,
) -> dict[str, Any]:
    """
    Processa uma pergunta em linguagem natural.

    Fluxo de roteamento por cliente/plataforma:
      1. NLP detectou cliente_key OU plataforma  → query filtrada
      2. Canal tem contexto de cliente definido  → query filtrada pelo canal
      3. Nenhum dos acima                        → executar_por_segmento()
                                                   (uma seção por segmento ativo)

    POD Kombucha é roteado pelo NLP (cliente_key='pod_kombucha') via fluxo principal.
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

    intents      = params["intents"]
    campanha     = params["campanha"]
    data_inicio  = params["data_inicio"]
    data_fim     = params["data_fim"]
    limit        = params["limit"]
    campanha_raw = params.get("raw", {}).get("campanha")
    periodo_nota = params.get("periodo_nota")

    # Melhoria 5: aplicar preferências do usuário se período não especificado
    try:
        from agent.user_prefs import aplicar_prefs as _aplicar_prefs
        data_inicio, data_fim = _aplicar_prefs(user_id or "", data_inicio, data_fim)
    except Exception as _up_err:
        logger.debug("[engine] user_prefs.aplicar_prefs: %s", _up_err)

    # ── Detecta cliente_key e plataforma ─────────────────────────────────────────
    # Prioridade: NLP > canal > None (None ativa modo segmentado)
    nlp_raw_full = params.get("raw", {})
    cliente_key  = nlp_raw_full.get("cliente_key") or None
    plataforma   = nlp_raw_full.get("plataforma")  or None

    # Infere cliente pelo canal se NLP não detectou
    canal_cliente = get_contexto_cliente(channel or "")
    if not cliente_key and canal_cliente:
        cliente_key = canal_cliente
        logger.debug("[consultar] cliente_key inferido pelo canal '%s': %s", channel, cliente_key)

    # Default para source_of quando não há contexto de cliente
    # Evita misturar dados de clientes diferentes em perguntas genéricas
    if not cliente_key and not plataforma:
        cliente_key = "source_of"
        logger.debug("[consultar] cliente_key não detectado — default: source_of")

    # Flag: precisa de execução segmentada?
    modo_segmentado = False  # desativado — default source_of garante isolamento

    # 3. Valida campanha
    if campanha is None and campanha_raw and "performance" in " ".join(intents):
        sugestoes = campanhas_disponiveis[:8]
        lista = "\n".join(f"  • {c}" for c in sugestoes)
        erro_campanha = (
            f"⚠️ Não encontrei nenhuma campanha com o nome *{campanha_raw}*.\n\n"
            f"*Campanhas disponíveis (por investimento):*\n{lista}\n"
            f"...e mais {max(0, len(campanhas_disponiveis) - 8)} outras.\n\n"
            f"_Tente usar parte do nome, ex: `performance Advantage` ou `performance ABO`_"
        )
        return {
            "intent":      intents[0],
            "intents":     intents,
            "dados":       [],
            "dados_extra": {},
            "meta":        {"intent": intents[0], "intents": intents,
                            "campanha": campanha_raw, "data_inicio": data_inicio,
                            "data_fim": data_fim, "limit": limit},
            "erro":        erro_campanha,
        }

    campanha_valida, erro_campanha = _validar_campanha(campanha, campanhas_disponiveis)
    if erro_campanha:
        return {
            "intent":       intents[0],
            "intents":      intents,
            "dados":        [],
            "dados_extra":  {},
            "meta":         {"intent": intents[0], "intents": intents,
                             "campanha": campanha, "data_inicio": data_inicio,
                             "data_fim": data_fim, "limit": limit},
            "erro":         erro_campanha,
        }

    campanha = campanha_valida
    meta = {
        "intents":         intents,
        "campanha":        campanha,
        "data_inicio":     data_inicio,
        "data_fim":        data_fim,
        "limit":           limit,
        "nlp_raw":         nlp_raw_full,
        "intent":          intents[0],
        "periodo_nota":    periodo_nota,
        "cliente_key":     cliente_key,
        "cliente":         cliente_key,  # alias para compatibilidade com handlers
        "plataforma":      plataforma,
        "modo_segmentado": modo_segmentado,
    }

    # 4. Executa queries
    _SEMANA_PARCIAL_SOF = re.compile(
        r"(essa\s+semana|esta\s+semana|semana\s+at[eé]\s+agora|semana\s+atual"
        r"|at[eé]\s+agora|at[eé]\s+hoje|semana\s+corrente)",
        re.IGNORECASE,
    )
    pedir_breakdown_sof = bool(_SEMANA_PARCIAL_SOF.search(pergunta))

    # ── Modo segmentado: sem cliente/plataforma detectados ───────────────────
    if modo_segmentado:
        segmentos = executar_por_segmento(
            intents[0],
            {"campanha": campanha, "data_inicio": data_inicio,
             "data_fim": data_fim, "limit": limit},
            meta_extra=meta,
        )
        # Intents secundárias: roda em modo segmentado também
        dados_extra_seg: dict = {}
        for intent_extra in intents[1:]:
            dados_extra_seg[intent_extra] = executar_por_segmento(
                intent_extra,
                {"campanha": campanha, "data_inicio": data_inicio,
                 "data_fim": data_fim, "limit": limit},
                meta_extra=meta,
            )
        resultado = {
            "intent":          intents[0],
            "intents":         intents,
            "dados":           segmentos,
            "dados_extra":     dados_extra_seg,
            "meta":            meta,
            "modo_segmentado": True,
            "aviso_vazio":     None,
            "delta":           {},
            "shopify":         {},
        }
        todos_vazios = all(s.get("vazio", True) for s in segmentos)
        if todos_vazios:
            resultado["aviso_vazio"] = (
                "⚠️ Nenhum dado encontrado para nenhum segmento no período solicitado."
            )
        if not todos_vazios:
            cache_set(pergunta, resultado)
        return resultado

    # ── Modo normal: cliente/plataforma conhecidos ───────────────────────────
    dados = _executar_intent(
        intents[0], campanha, data_inicio, data_fim, limit, meta,
        cliente_key=cliente_key, plataforma=plataforma,
    )

    # Item 1: Breakdown diário Source OF via ads_diario
    if pedir_breakdown_sof and isinstance(dados, (list, dict)):
        try:
            import sqlite3 as _sql3
            from db.connection import DB_PATH as _DB_PATH
            _conn = get_conn()
            _conn.row_factory = _sql3.Row
            _DIAS_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
            _dias = _conn.execute("""
                SELECT
                    data_inicio                                     AS dia,
                    SUM(investimento)                               AS invest,
                    SUM(valor_compras)                              AS receita,
                    CASE WHEN SUM(investimento) > 0
                         THEN SUM(valor_compras) / SUM(investimento) END AS roas,
                    SUM(compras)                                    AS conversoes,
                    CASE WHEN SUM(impressoes) > 0
                         THEN CAST(SUM(cliques_link) AS REAL)
                              / SUM(impressoes) * 100 END AS ctr
                FROM campanhas_unificada
                WHERE data_inicio BETWEEN ? AND ?
                GROUP BY data_inicio
                ORDER BY data_inicio ASC
            """, (data_inicio, data_fim)).fetchall()
            if _dias:
                bd_sof = []
                for _d in _dias:
                    _dd = dict(_d)
                    try:
                        from datetime import date as _ddate
                        _obj = _ddate.fromisoformat(_dd["dia"])
                        _dd["dia_nome"] = _DIAS_PT[_obj.weekday()]
                    except Exception:
                        _dd["dia_nome"] = ""
                    bd_sof.append(_dd)
                if isinstance(dados, dict):
                    dados["breakdown_diario"] = bd_sof
                else:
                    meta["breakdown_diario"] = bd_sof
        except Exception as _e:
            logger.debug("[SOF] Breakdown diário falhou: %s", _e)

    dados_extra = {}
    for intent_extra in intents[1:]:
        dados_extra[intent_extra] = _executar_intent(
            intent_extra, campanha, data_inicio, data_fim, limit, meta,
            cliente_key=cliente_key, plataforma=plataforma,
        )

    # 5. Verifica dados vazios e sugere alternativa
    dados_vazios_msg = None
    dados_principais_vazios = (
        (isinstance(dados, list) and len(dados) == 0) or
        (isinstance(dados, dict) and not dados)
    )

    # ── SQL dinâmico: fallback para intent desconhecida ou dados vazios em intent não mapeada ──
    _sql_dinamico_usado = False
    if dados_principais_vazios and intents and intents[0] in ("desconhecida", "resumo_geral"):
        # Tenta SQL dinâmico apenas se a pergunta parece conter filtros ad-hoc
        # (evita rodar LLM extra em perguntas simples sem dados no período)
        _ADOC_HINTS = re.compile(
            r'(acima\s+de|abaixo\s+de|maior\s+que|menor\s+que|entre\s+\d'
            r'|mais\s+de\s+\d|menos\s+de\s+\d|maior\s+ou\s+igual'
            r'|filtrar?|onde\s+o?\s*(roas|ctr|cpm|cpa|compras|cliques|invest)'
            r'|com\s+(roas|ctr|cpm|cpa)\s*(>|<|=|acima|abaixo))',
            re.IGNORECASE
        )
        if intents[0] == "desconhecida" or _ADOC_HINTS.search(pergunta):
            logger.info("[engine] Intent '%s' + pergunta ad-hoc — tentando SQL dinâmico", intents[0])
            try:
                from agent.sql_generator import executar_sql_dinamico
                sql_result = executar_sql_dinamico(
                    pergunta=pergunta,
                    cliente_key=cliente_key or "source_of",
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                )
                if sql_result["dados"] and not sql_result["erro"]:
                    dados = sql_result["dados"]
                    dados_principais_vazios = False
                    _sql_dinamico_usado = True
                    meta["sql_dinamico"] = sql_result["sql"]
                    meta["sql_latencia_ms"] = sql_result["latencia_ms"]
                    logger.info(
                        "[engine] SQL dinâmico OK — %d linhas em %dms",
                        len(dados), sql_result["latencia_ms"],
                    )
                elif sql_result["erro"]:
                    dados_vazios_msg = sql_result["erro"]
            except Exception as _sql_err:
                logger.warning("[engine] SQL dinâmico falhou: %s", _sql_err)

    if dados_principais_vazios:
        dados_vazios_msg = _dados_vazios_sugestao(intents[0], campanha, data_inicio, data_fim)

    # Melhoria 1: verificar gap de dados e adicionar aviso na resposta
    try:
        from agent.queries import verificar_gap_dados
        _gap = verificar_gap_dados(
            cliente_key=cliente_key or "source_of",
            plataforma=plataforma or "meta_ads"
        )
        if _gap.get("tem_gap") and _gap.get("aviso"):
            aviso_gap = _gap["aviso"]
            # Injetar no aviso_vazio existente ou criar novo campo
            if not dados_vazios_msg:
                dados_vazios_msg = aviso_gap
            else:
                dados_vazios_msg = aviso_gap + "\n\n" + dados_vazios_msg
    except Exception as _gap_err:
        logger.debug("[engine] verificar_gap_dados: %s", _gap_err)

    # Melhoria 6 + Item 2: Delta vs período anterior
    # Para resumo_geral: sempre calcula delta com queries.resumo_periodo para consistência
    # Para outros intents: usa ads_diario direto (comportamento legado)
    delta_consulta = {}
    if not campanha and data_inicio and data_fim and not dados_principais_vazios:
        # --- Melhoria 6: comparativo automático para resumo_geral ---
        if "resumo_geral" in intents and isinstance(dados, dict):
            try:
                from datetime import date as _date, timedelta as _td
                ini = _date.fromisoformat(data_inicio)
                fim = _date.fromisoformat(data_fim)
                delta_dias = (fim - ini).days + 1
                ant_fim = ini - _td(days=1)
                ant_ini = ant_fim - _td(days=delta_dias - 1)

                from agent.queries import resumo_periodo as _rp_m6
                dados_ant = _rp_m6(str(ant_ini), str(ant_fim), cliente_key=cliente_key, plataforma=plataforma)

                if dados_ant and dados_ant.get("investimento_total"):
                    def _pct(atual, ant):
                        if not ant or ant == 0:
                            return None
                        return round((atual - ant) / ant * 100, 1)

                    delta_consulta = {
                        "investimento": (
                            dados.get("investimento_total", 0),
                            dados_ant.get("investimento_total", 0),
                            _pct(dados.get("investimento_total", 0), dados_ant.get("investimento_total", 0)),
                        ),
                        "receita": (
                            dados.get("receita_total", 0),
                            dados_ant.get("receita_total", 0),
                            _pct(dados.get("receita_total", 0), dados_ant.get("receita_total", 0)),
                        ),
                        "roas": (
                            dados.get("roas", 0),
                            dados_ant.get("roas", 0),
                            _pct(dados.get("roas", 0), dados_ant.get("roas", 0)),
                        ),
                        "compras": (
                            dados.get("compras_total", 0),
                            dados_ant.get("compras_total", 0),
                            _pct(dados.get("compras_total", 0), dados_ant.get("compras_total", 0)),
                        ),
                    }
                    logger.debug("[engine] delta automático resumo_geral: %s", {k: v[2] for k, v in delta_consulta.items()})
            except Exception as _de:
                logger.debug("[engine] delta automático falhou (não crítico): %s", _de)
        else:
            # Comparativo via campanhas_unificada (fonte atual)
            try:
                from datetime import date as _ddate, timedelta as _dtd
                _d0 = _ddate.fromisoformat(data_inicio)
                _d1 = _ddate.fromisoformat(data_fim)
                _span = (_d1 - _d0).days + 1
                _ant0 = _d0 - _dtd(days=_span)
                _ant1 = _d1 - _dtd(days=_span)
                import sqlite3 as _sql3
                _c = get_conn()
                _c.row_factory = _sql3.Row
                _row_ant = _c.execute("""
                    SELECT
                        SUM(investimento) AS invest,
                        CASE WHEN SUM(investimento)>0
                             THEN SUM(valor_compras)/SUM(investimento) END AS roas,
                        SUM(compras) AS conversoes
                    FROM campanhas_unificada
                    WHERE data_inicio BETWEEN ? AND ?
                """, (str(_ant0), str(_ant1))).fetchone()
                # Extrai atuais
                _invest_a = None; _roas_a = None; _conv_a = None
                if isinstance(dados, dict):
                    _invest_a = dados.get("invest") or dados.get("total_invest")
                    _roas_a   = dados.get("roas")
                    _conv_a   = dados.get("compras") or dados.get("conversoes")
                elif isinstance(dados, list) and dados:
                    _invest_a = sum((r.get("investimento") or 0) for r in dados if isinstance(r, dict))
                    _roas_a   = sum((r.get("roas") or 0) for r in dados if isinstance(r, dict)) / len(dados) if dados else None
                if _row_ant and _row_ant["invest"]:
                    def _pct_leg(a, b): return ((a - b) / b * 100) if b else None
                    if _invest_a and _row_ant["invest"]:
                        delta_consulta["invest"] = (_invest_a, _row_ant["invest"], _pct_leg(_invest_a, _row_ant["invest"]))
                    if _roas_a and _row_ant["roas"]:
                        delta_consulta["roas"] = (_roas_a, _row_ant["roas"], _pct_leg(_roas_a, _row_ant["roas"]))
                    if _conv_a and _row_ant["conversoes"]:
                        delta_consulta["conversoes"] = (_conv_a, _row_ant["conversoes"], _pct_leg(_conv_a, _row_ant["conversoes"]))
            except Exception as _e:
                logger.debug("[Delta consulta] Falhou: %s", _e)

    # ── Enriquecimento com receita Shopify real ─────────────────────────────
    shopify_dados = {}
    if data_inicio and data_fim and not dados_principais_vazios:
        try:
            from agent.queries import receita_shopify_periodo as _rsp
            _sd = _rsp(data_inicio, data_fim)
            if _sd.get("sincronizado"):
                shopify_dados = _sd
                # Se temos receita Shopify real e dados Meta, recalcula ROAS real
                roas_real = _sd.get("roas_real")
                if roas_real and isinstance(dados, dict):
                    dados["roas_shopify"]           = roas_real
                    dados["receita_shopify"]        = _sd.get("receita_total")
                    dados["cac"]                    = _sd.get("cac")
                    dados["novos_clientes_shopify"] = _sd.get("novos_clientes")
                    dados["ticket_medio_shopify"]   = _sd.get("ticket_medio")
                    dados["taxa_recompra_pct"]      = _sd.get("taxa_recompra_pct")
                logger.debug("[engine] Shopify enriquecido: receita=%.2f roas_real=%s",
                             _sd.get("receita_total", 0), _sd.get("roas_real"))
        except Exception as _se:
            logger.debug("[engine] Shopify sync não disponível: %s", _se)

    resultado = {
        "intent":          intents[0],
        "intents":         intents,
        "dados":           dados,
        "dados_extra":     dados_extra,
        "meta":            meta,
        "aviso_vazio":     dados_vazios_msg,
        "delta":           delta_consulta,  # comparativo vs período anterior
        "shopify":         shopify_dados,   # receita real Shopify (vazio se não sincronizado)
    }

    # 6. Salva no cache (não cacheia resultados vazios)
    if not dados_principais_vazios:
        cache_set(pergunta, resultado)

    # Melhoria 5: atualizar preferências do usuário
    if not dados_principais_vazios and intents and intents[0] != "desconhecida":
        try:
            from agent.user_prefs import atualizar_prefs as _atualizar_prefs
            _atualizar_prefs(user_id or "", intents[0], data_inicio, data_fim, cliente_key)
        except Exception as _up_err:
            logger.debug("[engine] user_prefs.atualizar: %s", _up_err)

    # Log centralizado no handler Slack (tem resposta completa + latência ponta-a-ponta)
    return resultado


def consultar_sql(sql: str) -> list[dict]:
    """Executa SQL direto (apenas SELECT). Para debug/consultas avançadas."""
    sql_clean = sql.strip()
    if not sql_clean.upper().startswith("SELECT"):
        raise ValueError("Apenas SELECT é permitido via consultar_sql()")
    return raw_query(sql_clean)


# ── Multi-agentes: Fase 1 ──────────────────────────────────────────────────────────────
#
# OrchestratorAgent é a nova arquitetura. Por compatibilidade total,
# a função consultar() acima permanece intacta como implementação de
# referência. O orquestrador a substitui gradualmente.
#
# Para usar diretamente:
#   from agent.orchestrator_agent import OrchestratorAgent
#   orq = OrchestratorAgent()
#   resultado = orq.consultar(pergunta)
#
# Os handlers Slack já usam a função consultar() deste módulo —
# que em produção delegará ao orquestrador quando o feature flag
# MULTI_AGENT=1 estiver ativo.

_orchestrator_instance: "OrchestratorAgent | None" = None


def _get_orchestrator():
    global _orchestrator_instance
    if _orchestrator_instance is None:
        from agent.orchestrator_agent import OrchestratorAgent
        _orchestrator_instance = OrchestratorAgent()
    return _orchestrator_instance


def consultar_v2(
    pergunta: str,
    thread_ts: Optional[str] = None,
    channel: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict:
    """
    Nova implementação de consultar() via OrchestratorAgent.

    Delega inteiramente para o orquestrador da Fase 1.
    Compatível com a mesma assinatura e formato de retorno de consultar().
    """
    return _get_orchestrator().consultar(
        pergunta=pergunta,
        thread_ts=thread_ts,
        channel=channel,
        user_id=user_id,
    )

