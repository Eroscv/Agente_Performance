"""
agent/nlp.py

NLP com Claude — interpreta a pergunta do usuário e retorna
parâmetros estruturados para o engine executar as queries corretas.

v3 — melhorias:
  - max_tokens 256 → 512 (evita truncamento em perguntas complexas)
  - Few-shot examples no prompt (melhora precisão em casos ambíguos)
  - Retry automático em JSON inválido (até 2 tentativas)
  - Cache de resultado NLP por pergunta normalizada (evita chamadas duplas)
  - Logging de latência do Claude
  - Log de aviso quando intent desconhecida é retornada
  - Contexto de thread estruturado como mensagem separada no histórico
  - fuzzy threshold adaptativo por tamanho do fragmento
  - limit=None inferido quando usuário pede "todas" / "tudo"
"""

import json
import logging
import os
import re
import threading
import time
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

# Cache simples em memória: pergunta_normalizada → resultado NLP
# TTL implícito: invalidado quando o processo reinicia (OK — perguntas são efêmeras)
_nlp_cache: dict[str, dict] = {}
_nlp_cache_lock = threading.Lock()
NLP_CACHE_MAX = 256  # máximo de entradas em memória

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

Sua tarefa: analisar a pergunta do usuário e retornar SOMENTE um JSON estruturado com os parâmetros corretos.

## Intenções disponíveis

### Básicas
- listar_campanhas: listar todas as campanhas disponíveis
- resumo_geral: visão geral de todas as campanhas (resumo por campanha)
- performance_campanha: desempenho de uma campanha específica ao longo do tempo (série diária)
- periodo: resumo consolidado totalizando um período específico (sem campanha específica)
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
- saturacao_frequencia: campanhas com frequência alta (público saturado)
- eficiencia_criativo: qualidade do criativo — CPM, CTR, hook rate, retenção de vídeo
- distribuicao_investimento: share de orçamento por campanha (% do total)
- metricas_video: métricas de vídeo (views, ThruPlays, hook rate, custo por view)
- metricas_engajamento: curtidas, comentários, compartilhamentos, salvamentos
- metricas_mensageria: conversas iniciadas (WhatsApp/Messenger), custo por conversa
- comparar: comparação lado a lado entre campanhas específicas mencionadas
- comparar_periodos: comparar dois períodos (ex: maio vs junho, semana passada vs essa semana)
- comparar_wow: evolução semana a semana com variação % (week over week)
- comparar_mom: evolução mês a mês com variação % (month over month)
- comparar_antes_depois: comparar antes e depois de uma data específica

## Regras
1. Retorne MÚLTIPLAS intenções apenas se a pergunta pedir explicitamente mais de uma coisa
2. Para campanha: extraia o nome ou fragmento mencionado. Se não houver campanha específica, use null
3. Para datas: converta para YYYY-MM-DD. Hoje é {hoje}. Use as dicas de período no [contexto] quando presentes
4. Se não houver data mencionada, retorne null para ambas
5. Para limit: extraia número se mencionado (ex: "top 5" → 5). Se pedir "todas" / "tudo" → use 999. Senão, use 10
6. Para comparar_periodos: extraia periodo_a_inicio, periodo_a_fim, periodo_b_inicio, periodo_b_fim
7. Para ranking_metrica: campo "metrica" com o nome canônico (roas, cpc, ctr, cpm, compras, receita, investimento, frequencia, cliques, carrinho, checkout, custo_por_compra)
8. Para comparar_antes_depois: campo "data_virada" (YYYY-MM-DD)
9. Use SOMENTE intents da lista acima. Nunca invente novas.

## Exemplos (few-shot)

Pergunta: "qual a performance da campanha ABO em junho?"
Resposta: {"intents":["performance_campanha"],"campanha":"ABO","data_inicio":"2026-06-01","data_fim":"2026-06-30","periodo_a_inicio":null,"periodo_a_fim":null,"periodo_b_inicio":null,"periodo_b_fim":null,"data_virada":null,"metrica":null,"limit":10}

Pergunta: "top 5 campanhas por ROAS no mês passado"
Resposta: {"intents":["top_roas"],"campanha":null,"data_inicio":"2026-06-01","data_fim":"2026-06-30","periodo_a_inicio":null,"periodo_a_fim":null,"periodo_b_inicio":null,"periodo_b_fim":null,"data_virada":null,"metrica":null,"limit":5}

Pergunta: "compare maio com junho"
Resposta: {"intents":["comparar_periodos"],"campanha":null,"data_inicio":null,"data_fim":null,"periodo_a_inicio":"2026-05-01","periodo_a_fim":"2026-05-31","periodo_b_inicio":"2026-06-01","periodo_b_fim":"2026-06-30","data_virada":null,"metrica":null,"limit":10}

Pergunta: "quais campanhas estão sem conversão essa semana?"
Resposta: {"intents":["sem_conversao"],"campanha":null,"data_inicio":"2026-07-09","data_fim":"2026-07-15","periodo_a_inicio":null,"periodo_a_fim":null,"periodo_b_inicio":null,"periodo_b_fim":null,"data_virada":null,"metrica":null,"limit":10}

Pergunta: "mostre todas as campanhas ativas"
Resposta: {"intents":["listar_campanhas"],"campanha":null,"data_inicio":null,"data_fim":null,"periodo_a_inicio":null,"periodo_a_fim":null,"periodo_b_inicio":null,"periodo_b_fim":null,"data_virada":null,"metrica":null,"limit":999}

Pergunta: "o funil da Advantage Hidratante está vazando onde?"
Resposta: {"intents":["funil_detalhado"],"campanha":"Advantage Hidratante","data_inicio":null,"data_fim":null,"periodo_a_inicio":null,"periodo_a_fim":null,"periodo_b_inicio":null,"periodo_b_fim":null,"data_virada":null,"metrica":null,"limit":10}

Pergunta: "qual campanha tem maior alcance em abril?"
Resposta: {"intents":["top_alcance"],"campanha":null,"data_inicio":"2026-04-01","data_fim":"2026-04-30","periodo_a_inicio":null,"periodo_a_fim":null,"periodo_b_inicio":null,"periodo_b_fim":null,"data_virada":null,"metrica":null,"limit":10}

Pergunta: "a frequência da ABO está alta?"
Resposta: {"intents":["saturacao_frequencia"],"campanha":"ABO","data_inicio":null,"data_fim":null,"periodo_a_inicio":null,"periodo_a_fim":null,"periodo_b_inicio":null,"periodo_b_fim":null,"data_virada":null,"metrica":null,"limit":10}

## Formato de resposta (JSON puro, sem markdown, sem explicação)
{
  "intents": ["intent1"],
  "campanha": "fragmento ou null",
  "data_inicio": "YYYY-MM-DD ou null",
  "data_fim": "YYYY-MM-DD ou null",
  "periodo_a_inicio": null,
  "periodo_a_fim": null,
  "periodo_b_inicio": null,
  "periodo_b_fim": null,
  "data_virada": null,
  "metrica": null,
  "limit": 10
}"""


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def fuzzy_match_campaign(fragmento: str, campanhas: list[str]) -> Optional[str]:
    """
    Encontra a campanha mais próxima do fragmento.

    Threshold adaptativo por tamanho do fragmento:
    - < 4 chars  → threshold 90 (match quase exato, evita falsos positivos)
    - 4-7 chars  → threshold 75
    - >= 8 chars → threshold 60
    """
    if not fragmento or not campanhas:
        return None

    frag_lower = fragmento.lower().strip()

    # Match direto por substring (mais confiável)
    for c in campanhas:
        if frag_lower in c.lower():
            return c

    # Threshold adaptativo
    n = len(frag_lower)
    if n < 4:
        cutoff = 90
    elif n < 8:
        cutoff = 75
    else:
        cutoff = 60

    # Fuzzy match contra nomes completos
    result = fuzz_process.extractOne(
        fragmento,
        campanhas,
        scorer=fuzz.partial_ratio,
        score_cutoff=cutoff,
    )
    if result:
        logger.debug("[fuzzy] '%s' → '%s' (score=%s)", fragmento, result[0], result[1])
        return result[0]

    # Fuzzy match contra tokens individuais
    token_cutoff = max(cutoff, 70)
    for campanha in campanhas:
        tokens = re.split(r"[_\s\-/]+", campanha.lower())
        for token in tokens:
            if len(token) > 3:
                score = fuzz.ratio(frag_lower, token)
                if score >= token_cutoff:
                    logger.debug("[fuzzy-token] '%s' → token '%s' em '%s' (score=%s)", fragmento, token, campanha, score)
                    return campanha

    return None


def _nlp_cache_get(key: str) -> Optional[dict]:
    with _nlp_cache_lock:
        return _nlp_cache.get(key)


def _nlp_cache_set(key: str, value: dict) -> None:
    with _nlp_cache_lock:
        if len(_nlp_cache) >= NLP_CACHE_MAX:
            # Remove a entrada mais antiga (FIFO simples)
            oldest = next(iter(_nlp_cache))
            del _nlp_cache[oldest]
        _nlp_cache[key] = value


def _parse_claude_response(raw_text: str) -> dict:
    """Limpa e faz parse do JSON retornado pelo Claude."""
    # Remove blocos markdown se vier com ```json
    raw_text = re.sub(r"```(?:json)?\s*", "", raw_text).strip("` \n")
    # Remove comentários inline (// ...) que Claude às vezes insere
    raw_text = re.sub(r"//[^\n]*", "", raw_text)
    return json.loads(raw_text)


def interpretar(pergunta: str, campanhas_disponiveis: list[str], contexto: str = "") -> dict:
    """
    Usa Claude para interpretar a pergunta e retornar parâmetros estruturados.

    Melhorias v3:
    - Cache NLP por pergunta normalizada
    - max_tokens=512
    - Retry automático (até 2 tentativas) em JSON inválido
    - Contexto de thread como mensagem de usuário separada
    - Logging de latência
    - Aviso explícito quando intent desconhecida é retornada
    - limit=999 quando pergunta contém "todas" / "tudo"

    Fallback: regex simples se Claude falhar em todas as tentativas.
    """
    hoje = date.today().isoformat()

    # Normaliza sinônimos (enriquece com datas calculadas)
    pergunta_enriquecida = normalizar_pergunta(pergunta)

    # Chave de cache: pergunta enriquecida + contexto (resumido)
    cache_key = f"{pergunta_enriquecida}|ctx={contexto[:80] if contexto else ''}"
    cached = _nlp_cache_get(cache_key)
    if cached:
        logger.debug("[NLP] cache hit: '%s'", pergunta[:60])
        return cached

    # Monta histórico de mensagens para o Claude
    # Contexto de thread vira mensagem de user anterior → melhor coerência
    messages: list[dict] = []
    if contexto:
        messages.append({"role": "user",      "content": f"[Contexto da conversa anterior]\n{contexto}"})
        messages.append({"role": "assistant", "content": '{"intents":["resumo_geral"],"campanha":null,"data_inicio":null,"data_fim":null,"periodo_a_inicio":null,"periodo_a_fim":null,"periodo_b_inicio":null,"periodo_b_fim":null,"data_virada":null,"metrica":null,"limit":10}'})
    messages.append({"role": "user", "content": f"Pergunta: {pergunta_enriquecida}"})

    system = NLP_SYSTEM_PROMPT.replace("{hoje}", hoje)

    # Detecta "todas" / "tudo" para limit override
    limit_override = None
    if re.search(r"\btodas?\b|\btudo\b|\bcompleto\b|\binteiro\b", pergunta, re.I):
        limit_override = 999

    resultado = None
    t0 = time.monotonic()

    for tentativa in range(1, 3):  # até 2 tentativas
        try:
            client = get_client()
            model  = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

            response = client.messages.create(
                model=model,
                max_tokens=512,
                system=system,
                messages=messages,
            )

            raw_text = response.content[0].text.strip()
            parsed   = _parse_claude_response(raw_text)

            # Valida intents — loga qualquer intent desconhecida
            intents_brutas = parsed.get("intents", [])
            intents_invalidas = [i for i in intents_brutas if i not in INTENTS_DISPONIVEIS]
            if intents_invalidas:
                logger.warning(
                    "[NLP] Intent(s) desconhecida(s) retornada(s) pelo Claude: %s — ignoradas",
                    intents_invalidas
                )

            intents = [i for i in intents_brutas if i in INTENTS_DISPONIVEIS]
            if not intents:
                intents = ["resumo_geral"]

            # Fuzzy match de campanha
            campanha_raw = parsed.get("campanha")
            campanha     = None
            if campanha_raw:
                campanha = fuzzy_match_campaign(campanha_raw, campanhas_disponiveis)
                if campanha is None:
                    logger.info("[NLP] fuzzy match falhou para '%s'", campanha_raw)

            data_inicio  = parsed.get("data_inicio")
            data_fim     = parsed.get("data_fim")
            periodo_nota = None

            # Verifica se o período está dentro do range do banco
            if data_inicio or data_fim:
                try:
                    from agent.queries import data_range_disponivel
                    dr     = data_range_disponivel()
                    db_min = dr.get("data_minima")
                    db_max = dr.get("data_maxima")
                    if db_min and db_max and data_fim and data_fim > db_max:
                        logger.warning(
                            "[NLP] Período pedido (%s→%s) além do banco (%s→%s) — ajustando",
                            data_inicio, data_fim, db_min, db_max
                        )
                        from datetime import date as _date, timedelta as _td
                        try:
                            di       = _date.fromisoformat(data_inicio) if data_inicio else None
                            df       = _date.fromisoformat(data_fim)
                            db_max_d = _date.fromisoformat(db_max)
                            if di:
                                duracao    = (df - di).days
                                new_df     = db_max_d
                                new_di     = new_df - _td(days=duracao)
                                data_inicio = new_di.isoformat()
                                data_fim    = new_df.isoformat()
                            else:
                                data_fim = db_max
                            periodo_nota = (
                                f"⚠️ Período ajustado para {data_inicio} → {data_fim} "
                                f"(dados disponíveis até {db_max})"
                            )
                        except Exception as e2:
                            logger.debug("[NLP] falha ao ajustar período: %s", e2)
                except Exception as e:
                    logger.debug("[NLP] falha ao verificar range do banco: %s", e)

            latencia_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "[NLP] OK | tentativa=%d | %dms | intents=%s | campanha=%s | período=%s→%s",
                tentativa, latencia_ms, intents, campanha, data_inicio, data_fim,
            )

            resultado = {
                "intents":      intents,
                "campanha":     campanha,
                "data_inicio":  data_inicio,
                "data_fim":     data_fim,
                "limit":        limit_override if limit_override else int(parsed.get("limit", 10)),
                "raw":          parsed,
                "periodo_nota": periodo_nota,
            }
            break  # sucesso — sai do loop de retry

        except json.JSONDecodeError as e:
            logger.warning("[NLP] tentativa %d: JSON inválido (%s) — %s", tentativa, e, raw_text[:120] if 'raw_text' in dir() else "N/A")
            if tentativa == 2:
                logger.error("[NLP] Esgotadas %d tentativas, caindo no fallback regex", tentativa)
        except anthropic.APIError as e:
            logger.warning("[NLP] tentativa %d: APIError %s", tentativa, e)
            if tentativa == 2:
                logger.error("[NLP] API falhou em todas as tentativas")
            break  # erros de API não se beneficiam de retry imediato
        except Exception as e:
            logger.warning("[NLP] tentativa %d: erro inesperado %s", tentativa, e)
            break

    if resultado is None:
        return _fallback_regex(pergunta, campanhas_disponiveis, contexto=contexto)

    # Salva no cache
    _nlp_cache_set(cache_key, resultado)
    return resultado


def _fallback_regex(pergunta: str, campanhas: list[str], contexto: str = "") -> dict:
    """
    Fallback para regex simples se o Claude NLP falhar.

    Quando há contexto de thread, herda datas do histórico
    para perguntas de acompanhamento (ex: "e em maio?").
    """
    from agent.engine import _detect_intent, _extract_dates, _extract_limit, _extract_campaign
    intent       = _detect_intent(pergunta)
    d_ini, d_fim = _extract_dates(pergunta)
    campanha     = _extract_campaign(pergunta, campanhas)
    limit        = _extract_limit(pergunta)

    # Detecta "todas"
    if re.search(r"\btodas?\b|\btudo\b", pergunta, re.I):
        limit = 999

    # Herda datas do contexto se não encontrou na pergunta atual
    if (d_ini is None or d_fim is None) and contexto:
        ctx_ini, ctx_fim = _extract_dates(contexto)
        if d_ini is None:
            d_ini = ctx_ini
        if d_fim is None:
            d_fim = ctx_fim
        if ctx_ini or ctx_fim:
            logger.debug("[fallback_regex] Datas herdadas do contexto: %s → %s", d_ini, d_fim)

    logger.info("[NLP-fallback] intent=%s | campanha=%s | %s→%s", intent, campanha, d_ini, d_fim)

    return {
        "intents":     [intent],
        "campanha":    campanha,
        "data_inicio": d_ini,
        "data_fim":    d_fim,
        "limit":       limit,
        "raw":         {"fallback": True, "contexto_usado": bool(contexto)},
        "periodo_nota": None,
    }
