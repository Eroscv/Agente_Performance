"""
agent/clients/general.py

GeneralAgent — agente para perguntas sem cliente identificado.

Usado quando:
  - NLP não detectou cliente_key
  - Canal não tem mapeamento de cliente
  - Pergunta é genuinamente multi-cliente ("compare POD e Source OF")

Comportamento:
  - Executa execute_for_segment() para cada segmento ativo
  - Agrega resultados em seções separadas por cliente
  - Usa gerar_resposta() com prompt genérico
"""
from __future__ import annotations

import logging
from typing import Any

from agent.clients.base import BaseClientAgent

logger = logging.getLogger(__name__)


class GeneralAgent(BaseClientAgent):
    """Agente geral — multi-cliente, sem cliente fixo."""

    CLIENTE_KEY       = ""
    PLATAFORMA_PADRAO = "meta_ads"

    def execute(self, params: dict) -> dict:
        """
        Executa para todos os segmentos ativos em paralelo e agrega.
        """
        from agent.engine import _carregar_segmentos_ativos

        intent      = params.get("intent", "resumo_geral")
        intents     = params.get("intents", [intent])
        pergunta    = params.get("pergunta", "")
        contexto    = params.get("contexto", "")
        meta        = params.get("meta", {})

        segmentos = _carregar_segmentos_ativos()
        if not segmentos:
            logger.warning("[GeneralAgent] nenhum segmento ativo")
            return {
                "intent":      intent,
                "intents":     intents,
                "dados":       [],
                "dados_extra": {},
                "meta":        {**meta, "modo_segmentado": True},
                "erro":        "Nenhum cliente ativo encontrado.",
            }

        # Executa cada segmento
        resultados_seg = self._analysis.execute_for_segment(
            intent=intent,
            params=params,
            segmentos=segmentos,
            meta_extra=meta,
        )

        # Agrega dados e monta resposta por segmento
        dados_agregados = []
        secoes: list[str] = []

        for seg in resultados_seg:
            if seg.get("vazio") or seg.get("erro"):
                continue
            dados_seg = seg.get("dados") or []
            label     = seg.get("label", seg.get("cliente_key", "?"))
            emoji     = seg.get("emoji", "📊")

            dados_agregados.append({
                "label":       label,
                "cliente_key": seg["cliente_key"],
                "plataforma":  seg["plataforma"],
                "dados":       dados_seg,
            })

            # Gera resposta parcial por segmento
            try:
                from agent.claude_client import gerar_resposta
                meta_seg = {
                    **meta,
                    "cliente_key": seg["cliente_key"],
                    "cliente":     seg["cliente_key"],
                    "plataforma":  seg["plataforma"],
                }
                texto_seg = gerar_resposta(
                    pergunta=pergunta,
                    dados=dados_seg,
                    intent=intent,
                    meta=meta_seg,
                    contexto=contexto,
                    usar_kb=False,  # KB só para cliente específico
                )
                secoes.append(f"{emoji} *{label}*\n{texto_seg}")
            except Exception as e:
                logger.error("[GeneralAgent] gerar_resposta seg %s: %s", label, e)

        resposta_final = "\n\n─────────────────────\n\n".join(secoes) if secoes else (
            "⚠️ Não foi possível recuperar dados dos clientes no momento."
        )

        return {
            "intent":      intent,
            "intents":     intents,
            "dados":       dados_agregados,
            "dados_extra": {},
            "meta":        {**meta, "modo_segmentado": True},
            "resposta":    resposta_final,
        }
