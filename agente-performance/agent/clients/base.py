"""
agent/clients/base.py

BaseClientAgent — contrato que todos os agentes-cliente implementam.

Responsabilidade:
  1. Receber params já processados pelo OrchestratorAgent (intent, datas, campanha…)
  2. Buscar dados via agent.queries.* (sem LLM)
  3. Formatar resposta via agent.claude_client.gerar_resposta() com o system
     prompt do cliente
  4. Retornar dict compatível com o formato atual de consultar()

Cada subclasse só precisa sobrescrever:
  - CLIENTE_KEY       (str)
  - PLATAFORMA_PADRAO (str)
  - _get_system_prompt() → str
  - (opcional) _enriquecer() se o cliente tiver dados extras (ex: Shopify)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BaseClientAgent:
    """Agente base — não instanciar diretamente."""

    CLIENTE_KEY: str = ""
    PLATAFORMA_PADRAO: str = "meta_ads"

    def __init__(self) -> None:
        from agent.analysis_agent import AnalysisAgent
        self._analysis = AnalysisAgent()

    # ── Interface pública ─────────────────────────────────────────────────────

    def execute(self, params: dict) -> dict:
        """
        Ponto de entrada do subagente-cliente.

        Args:
            params: dict com chaves:
                pergunta, intent, intents, campanha, data_inicio, data_fim,
                limit, meta, contexto, user_id, thread_ts, cliente_key,
                plataforma, periodo_nota, nlp_raw, delta, shopify

        Returns:
            dict compatível com consultar(): {intent, intents, dados,
            dados_extra, meta, resposta?, erro?}
        """
        intent      = params.get("intent", "resumo_geral")
        intents     = params.get("intents", [intent])
        campanha    = params.get("campanha")
        data_inicio = params.get("data_inicio")
        data_fim    = params.get("data_fim")
        limit       = params.get("limit", 10)
        meta        = params.get("meta", {})
        contexto    = params.get("contexto", "")
        pergunta    = params.get("pergunta", "")

        # Resolve cliente_key e plataforma — usa os do params ou o padrão da classe
        # PLATAFORMA_PADRAO=None significa sem filtro de plataforma (retorna todas)
        cliente_key = params.get("cliente_key") or self.CLIENTE_KEY or None
        plataforma  = params.get("plataforma") or (self.PLATAFORMA_PADRAO if self.PLATAFORMA_PADRAO else None)

        logger.debug(
            "[%s] execute intent=%s cliente=%s plat=%s",
            self.__class__.__name__, intent, cliente_key, plataforma,
        )

        # 1. Busca dados via AnalysisAgent (queries SQL, sem LLM)
        try:
            dados = self._analysis.execute_intent(
                intent, campanha, data_inicio, data_fim, limit, meta,
                cliente_key=cliente_key, plataforma=plataforma,
            )
        except Exception as e:
            logger.error("[%s] execute_intent falhou: %s", self.__class__.__name__, e)
            dados = []

        # 2. Intents extras (multi-intent)
        dados_extra: dict[str, Any] = {}
        for intent_extra in intents[1:]:
            try:
                dados_extra[intent_extra] = self._analysis.execute_intent(
                    intent_extra, campanha, data_inicio, data_fim, limit, meta,
                    cliente_key=cliente_key, plataforma=plataforma,
                )
            except Exception as e:
                logger.debug("[%s] intent_extra %s falhou: %s", self.__class__.__name__, intent_extra, e)

        # 3. Enriquecimento específico do cliente (ex: Shopify na Source OF)
        enrich = self._enriquecer(params, dados, meta)

        # 4. Resposta via Claude com prompt do cliente
        try:
            from agent.claude_client import gerar_resposta
            resposta = gerar_resposta(
                pergunta=pergunta,
                dados=dados,
                intent=intent,
                meta={**meta, "cliente": cliente_key, "plataforma": plataforma},
                dados_extra=dados_extra or None,
                contexto=contexto,
                usar_kb=True,
                delta=params.get("delta"),
                **enrich,
            )
        except Exception as e:
            logger.error("[%s] gerar_resposta falhou: %s", self.__class__.__name__, e)
            resposta = f"⚠️ Erro ao gerar resposta: {e}"

        return {
            "intent":      intent,
            "intents":     intents,
            "dados":       dados,
            "dados_extra": dados_extra,
            "meta":        {**meta, "cliente_key": cliente_key, "plataforma": plataforma},
            "resposta":    resposta,
        }

    # ── Hooks para subclasses ─────────────────────────────────────────────────

    def _get_system_prompt(self) -> str:
        """Retorna o system prompt do cliente. Subclasses devem sobrescrever."""
        return ""

    def _enriquecer(self, params: dict, dados: Any, meta: dict) -> dict:
        """
        Hook de enriquecimento por cliente.
        Retorna kwargs extras para gerar_resposta() (ex: shopify=...).
        Subclasses sobrescrevem quando necessário.
        """
        return {}
