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
from datetime import date, timedelta
from typing import Optional

import anthropic
from dotenv import load_dotenv
from rapidfuzz import process as fuzz_process, fuzz

from agent.synonyms import normalizar_pergunta, PERIOD_SYNONYMS

load_dotenv()
logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None

INTENTS_DISPONIVEIS = [
    "listar_campanhas",
    "resumo_geral",
    "performance_campanha",
    "top_roas",
    "top_compras",
    "evolucao_diaria",
    "funil",
    "alertas_roas",
    "comparar",
    "periodo",
]

NLP_SYSTEM_PROMPT = """Você é um parser de linguagem natural para um sistema de análise de campanhas Meta Ads.

Sua tarefa: analisar a pergunta do usuário e retornar um JSON estruturado com os parâmetros para executar as consultas corretas.

## Intenções disponíveis
- listar_campanhas: listar todas as campanhas disponíveis
- resumo_geral: visão geral de todas as campanhas
- performance_campanha: desempenho de uma campanha específica ao longo do tempo
- top_roas: ranking de campanhas por ROAS (retorno sobre investimento)
- top_compras: ranking de campanhas por volume de compras/vendas
- evolucao_diaria: evolução dia a dia de métricas
- funil: métricas de funil (impressões → cliques → carrinho → checkout → compra)
- alertas_roas: campanhas com ROAS abaixo do esperado, com problemas
- comparar: comparação lado a lado entre campanhas
- periodo: resumo consolidado de um período específico

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

## Formato de resposta (JSON puro, sem markdown)
{
  "intents": ["intent1", "intent2"],
  "campanha": "fragmento mencionado ou null",
  "data_inicio": "YYYY-MM-DD ou null",
  "data_fim": "YYYY-MM-DD ou null",
  "limit": 10
}"""


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
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

        result = {
            "intents":     intents,
            "campanha":    campanha,
            "data_inicio": parsed.get("data_inicio"),
            "data_fim":    parsed.get("data_fim"),
            "limit":       int(parsed.get("limit", 10)),
            "raw":         parsed,
        }

        logger.info(
            "NLP: intents=%s | campanha=%s | período=%s→%s",
            intents, campanha, result["data_inicio"], result["data_fim"]
        )
        return result

    except (json.JSONDecodeError, anthropic.APIError, Exception) as e:
        logger.warning("NLP Claude falhou (%s), usando fallback regex", e)
        return _fallback_regex(pergunta, campanhas_disponiveis)


def _fallback_regex(pergunta: str, campanhas: list[str]) -> dict:
    """Fallback para regex simples se o Claude NLP falhar."""
    from agent.engine import _detect_intent, _extract_dates, _extract_limit, _extract_campaign
    intent     = _detect_intent(pergunta)
    d_ini, d_fim = _extract_dates(pergunta)
    campanha   = _extract_campaign(pergunta, campanhas)
    limit      = _extract_limit(pergunta)
    return {
        "intents":     [intent],
        "campanha":    campanha,
        "data_inicio": d_ini,
        "data_fim":    d_fim,
        "limit":       limit,
        "raw":         {"fallback": True},
    }
