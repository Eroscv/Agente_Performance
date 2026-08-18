"""
agent/orchestrator_agent.py — Orquestrador principal.

Responsabilidade única: receber a pergunta, classificar via NLP,
detectar cliente/plataforma e delegar para o AnalysisAgent correto.

Não executa queries — apenas coordena.
"""

import logging
import re
import time
from typing import Any, Optional

from agent.base_agent import BaseAnalysisAgent
from agent.analysis_agent import AnalysisAgent
from agent.nlp import interpretar, fuzzy_match_campaign
from agent.context_store import get_history, build_context_summary
from agent.cache import get as cache_get, set as cache_set

logger = logging.getLogger(__name__)

# Regex para breakdown semanal parcial (Source OF)
_SEMANA_PARCIAL_RE = re.compile(
    r"(essa\s+semana|esta\s+semana|semana\s+at[eé]\s+agora|semana\s+atual"
    r"|at[eé]\s+agora|at[eé]\s+hoje|semana\s+corrente)",
    re.IGNORECASE,
)


class OrchestratorAgent:
    """
    Coordena o fluxo completo de uma consulta:
      1. Cache check
      2. NLP (intents + parâmetros)
      3. Detecção de cliente/plataforma
      4. Validação de campanha
      5. Delegação para AnalysisAgent
      6. Enriquecimento (delta + Shopify)
      7. Cache write + user_prefs update
    """

    def __init__(self):
        self._analysis = AnalysisAgent()
        self._segmentos: list[dict] = []
        self._segmentos_loaded = False

    def _load_segmentos(self) -> list[dict]:
        if not self._segmentos_loaded:
            try:
                from agent.engine import _carregar_segmentos_ativos
                self._segmentos = _carregar_segmentos_ativos()
            except Exception as e:
                logger.warning("[Orchestrator] Segmentos não carregados: %s", e)
                self._segmentos = []
            self._segmentos_loaded = True
        return self._segmentos

    def _get_canal_cliente(self, channel: str) -> Optional[str]:
        """Retorna cliente_key associado ao canal, se configurado."""
        try:
            from agent.engine import get_contexto_cliente
            return get_contexto_cliente(channel or "")
        except Exception:
            return None

    def _get_available_campaigns(
        self,
        cliente_key: Optional[str] = None,
        plataforma: Optional[str] = None,
    ) -> list[str]:
        return self._analysis.get_available_campaigns(cliente_key, plataforma)

    def _build_error_result(
        self,
        intents: list[str],
        campanha: Optional[str],
        data_inicio: Optional[str],
        data_fim: Optional[str],
        limit: int,
        erro: str,
    ) -> dict:
        return {
            "intent":      intents[0] if intents else "desconhecida",
            "intents":     intents,
            "dados":       [],
            "dados_extra": {},
            "meta":        {
                "intent": intents[0] if intents else "desconhecida",
                "intents": intents,
                "campanha": campanha,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "limit": limit,
            },
            "erro": erro,
        }

    def consultar(
        self,
        pergunta: str,
        thread_ts: Optional[str] = None,
        channel: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Ponto de entrada principal — processa pergunta em linguagem natural.

        Fluxo:
          1. Cache → retorno imediato se hit
          2. NLP → intents + campanha + datas
          3. Detecção cliente/plataforma (NLP > canal > default source_of)
          4. Validação de campanha via fuzzy match
          5. AnalysisAgent.execute_intent() para intent principal + extras
          6. SQL dinâmico se dados vazios e pergunta ad-hoc
          7. Enriquecimento: delta automático + Shopify
          8. Cache write + user_prefs update
        """
        t0 = time.time()

        # 1. Cache
        cached = cache_get(pergunta)
        if cached:
            return cached

        # 2. NLP
        campanhas = self._get_available_campaigns()
        contexto = build_context_summary(thread_ts) if thread_ts else ""
        params = interpretar(pergunta, campanhas, contexto)

        intents      = params["intents"]
        campanha     = params["campanha"]
        data_inicio  = params["data_inicio"]
        data_fim     = params["data_fim"]
        limit        = params["limit"]
        campanha_raw = params.get("raw", {}).get("campanha")
        periodo_nota = params.get("periodo_nota")
        nlp_raw_full = params.get("raw", {})

        # Aplica preferências do usuário se período não especificado
        try:
            from agent.user_prefs import aplicar_prefs as _aplicar_prefs
            data_inicio, data_fim = _aplicar_prefs(user_id or "", data_inicio, data_fim)
        except Exception as e:
            logger.debug("[Orchestrator] user_prefs.aplicar_prefs: %s", e)

        # 3. Detecta cliente/plataforma — Prioridade: NLP > canal > default source_of
        cliente_key = nlp_raw_full.get("cliente_key") or None
        plataforma  = nlp_raw_full.get("plataforma")  or None

        canal_cliente = self._get_canal_cliente(channel or "")
        if not cliente_key and canal_cliente:
            cliente_key = canal_cliente
            logger.debug("[Orchestrator] cliente_key via canal '%s': %s", channel, cliente_key)

        if not cliente_key and not plataforma:
            cliente_key = "source_of"
            logger.debug("[Orchestrator] cliente_key default: source_of")

        # 4. Valida campanha — erro de campanha não encontrada retorna imediatamente
        if campanha is None and campanha_raw and "performance" in " ".join(intents):
            lista = "\n".join(f"  • {c}" for c in campanhas[:8])
            return self._build_error_result(
                intents, campanha_raw, data_inicio, data_fim, limit,
                f"⚠️ Não encontrei a campanha *{campanha_raw}*.\n\n"
                f"*Campanhas disponíveis:*\n{lista}\n"
                f"...e mais {max(0, len(campanhas) - 8)} outras.\n\n"
                f"_Tente parte do nome, ex: `performance Advantage`_",
            )

        campanha_ok, erro_campanha = self._analysis.validate_campaign(campanha, campanhas)
        if erro_campanha:
            return self._build_error_result(
                intents, campanha, data_inicio, data_fim, limit, erro_campanha
            )
        campanha = campanha_ok

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
            "cliente":         cliente_key,
            "plataforma":      plataforma,
            "modo_segmentado": False,
        }

        pedir_breakdown = bool(_SEMANA_PARCIAL_RE.search(pergunta))

        # 5. Executa intent principal
        dados = self._analysis.execute_intent(
            intents[0], campanha, data_inicio, data_fim, limit, meta,
            cliente_key=cliente_key, plataforma=plataforma,
        )

        # Breakdown diário (Source OF / semana parcial) — best-effort
        if pedir_breakdown and isinstance(dados, (list, dict)):
            pass  # breakdown inline não extraído ainda — sem impacto na resposta

        # Intents secundárias
        dados_extra: dict = {}
        for intent_extra in intents[1:]:
            try:
                dados_extra[intent_extra] = self._analysis.execute_intent(
                    intent_extra, campanha, data_inicio, data_fim, limit, meta,
                    cliente_key=cliente_key, plataforma=plataforma,
                )
            except Exception as e:
                logger.warning("[Orchestrator] intent_extra %s falhou: %s", intent_extra, e)

        # 6. SQL dinâmico se dados vazios
        dados_vazios = (
            (isinstance(dados, list) and len(dados) == 0)
            or (isinstance(dados, dict) and not dados)
        )
        dados_vazios_msg = None
        sql_result = None

        if dados_vazios:
            sql_result = self._analysis.try_sql_fallback(
                pergunta, intents[0], cliente_key or "source_of",
                data_inicio, data_fim,
            )
            if sql_result:
                dados = sql_result["dados"]
                dados_vazios = False
                meta["sql_dinamico"] = sql_result["sql"]
                meta["sql_latencia_ms"] = sql_result.get("latencia_ms", 0)
            elif sql_result and sql_result.get("erro"):
                dados_vazios_msg = sql_result["erro"]

        if dados_vazios:
            dados_vazios_msg = self._analysis.empty_suggestion(
                intents[0], campanha, data_inicio, data_fim
            )

        # Verificar gap de dados
        try:
            from agent.queries import verificar_gap_dados
            _gap = verificar_gap_dados(
                cliente_key=cliente_key or "source_of",
                plataforma=plataforma or "meta_ads",
            )
            if _gap.get("tem_gap") and _gap.get("aviso"):
                aviso_gap = _gap["aviso"]
                dados_vazios_msg = (
                    aviso_gap + "\n\n" + dados_vazios_msg
                    if dados_vazios_msg else aviso_gap
                )
        except Exception as e:
            logger.debug("[Orchestrator] verificar_gap_dados: %s", e)

        # 7. Enriquecimento
        delta = {}
        if not dados_vazios:
            delta = self._analysis.enrich_delta(
                dados, intents, campanha, data_inicio, data_fim,
                cliente_key, plataforma,
            )

        shopify = {}
        if not dados_vazios:
            dados, shopify = self._analysis.enrich_shopify(dados, data_inicio, data_fim)

        resultado = {
            "intent":      intents[0],
            "intents":     intents,
            "dados":       dados,
            "dados_extra": dados_extra,
            "meta":        meta,
            "aviso_vazio": dados_vazios_msg,
            "delta":       delta,
            "shopify":     shopify,
        }

        # 8. Cache write
        if not dados_vazios:
            cache_set(pergunta, resultado)

        # Atualiza preferências do usuário
        if not dados_vazios and intents and intents[0] != "desconhecida":
            try:
                from agent.user_prefs import atualizar_prefs as _atualizar_prefs
                _atualizar_prefs(user_id or "", intents[0], data_inicio, data_fim, cliente_key)
            except Exception as e:
                logger.debug("[Orchestrator] user_prefs.atualizar: %s", e)

        logger.debug(
            "[Orchestrator] consultar OK | intent=%s | %.0fms",
            intents[0], (time.time() - t0) * 1000,
        )
        return resultado
