"""
agent/nlp.py

NLP com Claude — interpreta a pergunta do usuário e retorna
parâmetros estruturados para o engine executar as queries corretas.

Substitui a detecção por regex do engine original.
Suporta múltiplas intenções numa mesma pergunta.

Retorna:
  {
    "intents":    list[str],          # ex: ["top_roas", "top_compras"]
    "campanha":   str | None,         # nome fuzzy-matched
    "data_inicio": str | None,        # YYYY-MM-DD
    "data_fim":    str | None,        # YYYY-MM-DD
    "limit":      int,                # default 10
    "raw":        dict                # resposta bruta do Claude
  }
"""

import json
import logging
import os
import re
import threading
from datetime import date, timedelta
from typing import Optional

import anthropic
from dotenv import load_dotenv
from rapidfuzz import process as fuzz_process, fuzz

from agent.synonyms import normalizar_pergunta, PERIOD_SYNONYMS

load_dotenv()
logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None
_client_lock = threading.Lock()

INTENTS_DISPONIVEIS = [
    # Básicas
    "listar_campanhas",
    "resumo_geral",
    "performance_campanha",
    "periodo",
    "evolucao_diaria",
    "historico_mensal",
    "resumo_executivo",
    # Rankings
    "top_roas",
    "top_compras",
    "top_receita",
    "top_ctr",
    "top_cpm",
    "top_alcance",
    "top_cliques",
    "ranking_metrica",
    # Análises específicas
    "funil",
    "funil_detalhado",
    "alertas_roas",
    "comparar",
    "comparar_periodos",
    "comparar_wow",
    "comparar_mom",
    "comparar_antes_depois",
    "eficiencia_criativo",
    "distribuicao_investimento",
    "sem_conversao",
    "campanhas_inativas",
    "variacao_roas",
    "saturacao_frequencia",
    "metricas_video",
    "metricas_engajamento",
    "metricas_mensageria",
]

NLP_SYSTEM_PROMPT = """Você é um parser de linguagem natural para um sistema de análise de campanhas Meta Ads.

Sua tarefa: analisar a pergunta do usuário e retornar um JSON estruturado com os parâmetros para executar as consultas corretas.

## Intenções disponíveis

### Básicas
- listar_campanhas: listar todas as campanhas disponíveis
- resumo_geral: visão geral de todas as campanhas (resumo por campanha)
- performance_campanha: desempenho de uma campanha específica ao longo do tempo (série diária)
- periodo: resumo consolidado totalizando um período específico
- evolucao_diaria: evolução dia a dia de métricas (gráfico temporal)
- historico_mensal: histórico mês a mês dos últimos N meses (sazonalidade, tendência)
- resumo_executivo: visão executiva completa com top campanhas + alertas em uma só resposta

### Rankings
- top_roas: ranking de campanhas por ROAS (retorno sobre investimento)
- top_compras: ranking de campanhas por volume de compras/vendas
- top_receita: ranking de campanhas por receita total gerada
- top_ctr: ranking de campanhas por CTR (taxa de cliques — qualidade do anúncio)
- top_cpm: ranking por menor CPM (custo de exibição mais barato)
- top_alcance: ranking por maior alcance único de pessoas
- top_cliques: ranking por volume de cliques no link
- ranking_metrica: ranking de todas as campanhas por qualquer métrica específica mencionada

### Análises específicas
- funil: métricas de funil resumidas (impressões → cliques → carrinho → checkout → compra)
- funil_detalhado: custo em cada etapa do funil + taxas de conversão entre etapas
- alertas_roas: campanhas com ROAS abaixo do esperado ou com problemas de conversão
- sem_conversao: campanhas gastando dinheiro mas sem nenhuma compra registrada
- campanhas_inativas: campanhas que pararam de rodar (sem dados recentes)
- variacao_roas: volatilidade diária do ROAS — identifica picos e quedas
- saturacao_frequencia: campanhas com frequência alta (público vendo o anúncio muitas vezes — saturação)
- eficiencia_criativo: qualidade do criativo — CPM, CTR, hook rate, retenção de vídeo
- distribuicao_investimento: share de orçamento por campanha (% do total)
- metricas_video: métricas de vídeo (views, ThruPlays, hook rate, custo por view)
- metricas_engajamento: curtidas, comentários, compartilhamentos, salvamentos
- metricas_mensageria: conversas iniciadas (WhatsApp/Messenger), custo por conversa
- comparar: comparação lado a lado entre campanhas específicas mencionadas
- comparar_periodos: comparar dois períodos (ex: maio vs junho, semana passada vs essa semana, 2025 vs 2026)
- comparar_wow: evolução semana a semana com variação % (week over week)
- comparar_mom: evolução mês a mês com variação % (month over month)
- comparar_antes_depois: comparar antes e depois de uma data específica

## Regras
1. Você pode retornar MÚLTIPLAS intenções se a pergunta pedir mais de uma coisa
2. Para campanha: extraia o nome ou fragmento mencionado (ex: "hidratante", "ABO", "black november")
3. Para datas: converta para YYYY-MM-DD. Hoje é {hoje}. Interprete:
   - "essa semana" / "últimos 7 dias" → data_inicio = hoje-7, data_fim = hoje
   - "esse mês" / "mês atual" → data_inicio = primeiro dia do mês, data_fim = hoje
   - "mês passado" / "último mês" → data_inicio = primeiro dia do mês passado, data_fim = último dia do mês passado
   - "em maio" / "maio" → 2026-05-01 a 2026-05-31
   - "em novembro" / "novembro" → 2025-11-01 a 2025-11-30
   - "black friday" → 2025-11-28 a 2025-12-02
   - "ontem" → data_inicio = data_fim = hoje-1
   - "em 2025" → 2025-01-01 a 2025-12-31
   - "segundo trimestre" → 2026-04-01 a 2026-06-30
4. Se não houver data mencionada, retorne null para ambas
5. Para limit: extraia número se mencionado (ex: "top 5"), senão use 10
6. Para comparar_periodos: extraia periodo_a_inicio, periodo_a_fim, periodo_b_inicio, periodo_b_fim
7. Para ranking_metrica: campo "metrica" com nome canônico
8. Para comparar_antes_depois: campo "data_virada" (YYYY-MM-DD)

## Formato de resposta (JSON puro, sem markdown)
{
  "intents": ["intent1", "intent2"],
  "campanha": "fragmento mencionado ou null",
  "data_inicio": "YYYY-MM-DD ou null",
  "data_fim": "YYYY-MM-DD ou null",
  "periodo_a_inicio": "YYYY-MM-DD ou null",
  "periodo_a_fim": "YYYY-MM-DD ou null",
  "periodo_b_inicio": "YYYY-MM-DD ou null",
  "periodo_b_fim": "YYYY-MM-DD ou null",
  "data_virada": "YYYY-MM-DD ou null",
  "metrica": "roas ou null",
  "limit": 10
}"""


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # double-checked locking
                _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def fuzzy_match_campaign(fragmento: str, campanhas: list[str]) -> Optional[str]:
    """
    Encontra a campanha mais próxima do fragmento mencionado.
    Usa rapidfuzz com threshold de 60 para tolerância razoável.
    """
    if not fragmento or not campanhas:
        return None

    # Tenta match direto primeiro
    frag_lower = fragmento.lower()
    for c in campanhas:
        if frag_lower in c.lower():
            return c

    # Fuzzy match contra nomes completos
    result = fuzz_process.extractOne(
        fragmento,
        campanhas,
        scorer=fuzz.partial_ratio,
        score_cutoff=60,
    )
    if result:
        return result[0]

    # Fuzzy match contra tokens individuais (ex: "hidratante" → "Advantage_HidratanteCorporal")
    for campanha in campanhas:
        tokens = re.split(r"[_\s\-/]+", campanha.lower())
        for token in tokens:
            if len(token) > 3:
                score = fuzz.ratio(frag_lower, token)
                if score >= 70:
                    return campanha

    return None


def interpretar(pergunta: str, campanhas_disponiveis: list[str], contexto: str = "") -> dict:
    """
    Usa Claude para interpretar a pergunta e retornar parâmetros estruturados.
    Fallback para regex simples se a API falhar.

    Args:
        pergunta: texto do usuário
        campanhas_disponiveis: lista de campanhas no banco
        contexto: histórico da thread para perguntas de acompanhamento

    Returns:
        dict com intents, campanha, data_inicio, data_fim, limit
    """
    hoje = date.today().isoformat()

    # Normaliza sinônimos
    pergunta_enriquecida = normalizar_pergunta(pergunta)

    # Monta prompt com contexto de thread se houver
    user_content = f"Pergunta: {pergunta_enriquecida}"
    if contexto:
        user_content = f"{contexto}\n\nNova pergunta: {pergunta_enriquecida}"

    try:
        client  = get_client()
        model   = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
        system  = NLP_SYSTEM_PROMPT.replace("{hoje}", hoje)

        response = client.messages.create(
            model=model,
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )

        raw_text = response.content[0].text.strip()

        # Remove blocos markdown se vier com ```json
        raw_text = re.sub(r"```(?:json)?\s*", "", raw_text).strip("` \n")

        parsed = json.loads(raw_text)

        # Valida intents
        intents = [i for i in parsed.get("intents", []) if i in INTENTS_DISPONIVEIS]
        if not intents:
            intents = ["resumo_geral"]

        # Fuzzy match de campanha
        campanha_raw = parsed.get("campanha")
        campanha     = None
        if campanha_raw:
            campanha = fuzzy_match_campaign(campanha_raw, campanhas_disponiveis)

        data_inicio = parsed.get("data_inicio")
        data_fim    = parsed.get("data_fim")
        periodo_nota = None

        # Verifica se o período pedido está dentro do range disponível no banco
        if data_inicio or data_fim:
            try:
                from agent.queries import data_range_disponivel
                dr = data_range_disponivel()
                db_min = dr.get("data_minima")
                db_max = dr.get("data_maxima")
                if db_min and db_max and data_fim and data_fim > db_max:
                    logger.warning(
                        "NLP: período pedido (%s→%s) além do range do banco (%s→%s) — ajustando para período mais recente",
                        data_inicio, data_fim, db_min, db_max
                    )
                    # Ajusta para o período mais recente disponível com mesma duração
                    from datetime import date as _date, timedelta as _td
                    try:
                        di = _date.fromisoformat(data_inicio) if data_inicio else None
                        df = _date.fromisoformat(data_fim)
                        db_max_d = _date.fromisoformat(db_max)
                        if di:
                            duracao = (df - di).days
                            new_df = db_max_d
                            new_di = new_df - _td(days=duracao)
                            data_inicio = new_di.isoformat()
                            data_fim    = new_df.isoformat()
                        else:
                            data_fim    = db_max
                        periodo_nota = (
                            f"⚠️ Período ajustado automaticamente para {data_inicio} → {data_fim} "
                            f"(dados disponíveis até {db_max})"
                        )
                    except Exception as e2:
                        logger.debug("NLP: falha ao ajustar período: %s", e2)
            except Exception as e:
                logger.debug("NLP: falha ao verificar range do banco: %s", e)

        result = {
            "intents":      intents,
            "campanha":     campanha,
            "data_inicio":  data_inicio,
            "data_fim":     data_fim,
            "limit":        int(parsed.get("limit", 10)),
            "raw":          parsed,
            "periodo_nota": periodo_nota,
        }

        logger.info(
            "NLP: intents=%s | campanha=%s | período=%s→%s%s",
            intents, campanha, result["data_inicio"], result["data_fim"],
            f" | nota='{periodo_nota}'" if periodo_nota else "",
        )
        return result

    except (json.JSONDecodeError, anthropic.APIError, Exception) as e:
        logger.warning("NLP Claude falhou (%s), usando fallback regex", e)
        return _fallback_regex(pergunta, campanhas_disponiveis, contexto=contexto)


def _fallback_regex(pergunta: str, campanhas: list[str], contexto: str = "") -> dict:
    """
    Fallback para regex simples se o Claude NLP falhar.

    Quando há contexto de thread, tenta extrair datas do histórico
    para perguntas de acompanhamento (ex: "e em maio?") que dependem
    do período mencionado anteriormente.
    """
    from agent.engine import _detect_intent, _extract_dates, _extract_limit, _extract_campaign
    intent       = _detect_intent(pergunta)
    d_ini, d_fim = _extract_dates(pergunta)
    campanha     = _extract_campaign(pergunta, campanhas)
    limit        = _extract_limit(pergunta)

    # Se não encontrou datas na pergunta atual mas há contexto de thread,
    # tenta extrair datas do histórico como fallback
    if (d_ini is None or d_fim is None) and contexto:
        ctx_ini, ctx_fim = _extract_dates(contexto)
        if d_ini is None:
            d_ini = ctx_ini
        if d_fim is None:
            d_fim = ctx_fim
        if ctx_ini or ctx_fim:
            logger.debug(
                "[fallback_regex] Datas herdadas do contexto de thread: %s → %s",
                d_ini, d_fim,
            )

    return {
        "intents":     [intent],
        "campanha":    campanha,
        "data_inicio": d_ini,
        "data_fim":    d_fim,
        "limit":       limit,
        "raw":         {"fallback": True, "contexto_usado": bool(contexto)},
    }
