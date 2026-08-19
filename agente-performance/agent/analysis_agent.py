"""
agent/analysis_agent.py — Agente de execução de análises.

Responsabilidade única: dado um intent + parâmetros → executa a query
correspondente e retorna os dados brutos.

Inclui:
  - Mapeamento intent → função queries.*
  - Enriquecimento com delta automático vs período anterior
  - Enriquecimento com receita Shopify real
  - Fallback SQL dinâmico para intents desconhecidas / perguntas ad-hoc
"""

import logging
import re
from typing import Any, Optional

from agent import queries
from agent.base_agent import BaseAnalysisAgent
from agent.comparatives import (
    comparar_periodos,
    comparar_campanhas_detalhado,
    comparar_semana_a_semana,
    comparar_mes_a_mes,
    comparar_antes_depois,
    ranking_comparativo,
)
from db.connection import get_conn

logger = logging.getLogger(__name__)

# Padrão que indica pergunta com filtros numéricos ad-hoc
_ADOC_HINTS_RE = re.compile(
    r'(acima\s+de|abaixo\s+de|maior\s+que|menor\s+que|entre\s+\d'
    r'|mais\s+de\s+\d|menos\s+de\s+\d|maior\s+ou\s+igual'
    r'|filtrar?|onde\s+o?\s*(roas|ctr|cpm|cpa|compras|cliques|invest)'
    r'|com\s+(roas|ctr|cpm|cpa)\s*(>|<|=|acima|abaixo))',
    re.IGNORECASE,
)


class AnalysisAgent(BaseAnalysisAgent):
    """
    Executa intents de análise e retorna dados brutos + enriquecimentos.

    Fluxo:
      1. execute_intent() → query fixa correspondente ao intent
      2. enrich_delta()   → delta automático vs período anterior
      3. enrich_shopify() → receita real Shopify (se disponível)
      4. fallback SQL dinâmico se dados vazios e pergunta ad-hoc
    """

    def execute(
        self,
        intent: str,
        campanha: Optional[str],
        data_inicio: Optional[str],
        data_fim: Optional[str],
        limit: int,
        meta: dict,
        cliente_key: Optional[str] = None,
        plataforma: Optional[str] = None,
    ) -> Any:
        return self.execute_intent(
            intent, campanha, data_inicio, data_fim, limit, meta,
            cliente_key=cliente_key, plataforma=plataforma,
        )

    def execute_intent(
        self,
        intent: str,
        campanha: Optional[str],
        data_inicio: Optional[str],
        data_fim: Optional[str],
        limit: int,
        meta_extra: dict = None,
        cliente_key: Optional[str] = None,
        plataforma: Optional[str] = None,
    ) -> Any:
        """Mapeia intent → função queries.* e executa."""
        _fk: dict = {}
        if cliente_key:
            _fk["cliente_key"] = cliente_key
        if plataforma:
            _fk["plataforma"] = plataforma

        if intent == "listar_campanhas":
            return queries.listar_campanhas(**_fk)
        elif intent in ("resumo_geral", "periodo"):
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
        elif intent == "funil":
            return queries.metricas_funil(campanha, data_inicio, data_fim, **_fk)
        elif intent == "funil_detalhado":
            return queries.custo_funil_detalhado(campanha, data_inicio, data_fim, **_fk)
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
            all_camps = self.get_available_campaigns(cliente_key, plataforma)
            encontradas = [c for c in all_camps if c.lower() in (campanha or "").lower()]
            if not encontradas and campanha:
                encontradas = [campanha]
            return comparar_campanhas_detalhado(encontradas, data_inicio, data_fim)
        elif intent == "comparar_periodos":
            nlp_raw = meta_extra.get("nlp_raw", {}) if meta_extra else {}
            ini_a = nlp_raw.get("periodo_a_inicio") or data_inicio
            fim_a = nlp_raw.get("periodo_a_fim") or data_fim
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
            nlp_raw = meta_extra.get("nlp_raw", {}) if meta_extra else {}
            data_virada = nlp_raw.get("data_virada") or data_inicio
            if data_virada:
                return comparar_antes_depois(data_virada, campanha)
            return queries.resumo_por_campanha(data_inicio, data_fim, **_fk)
        elif intent == "ranking_metrica":
            nlp_raw = meta_extra.get("nlp_raw", {}) if meta_extra else {}
            metrica = nlp_raw.get("metrica") or "roas"
            return ranking_comparativo(metrica, data_inicio, data_fim)
        elif intent == "comparar_plataformas":
            from agent.queries.comparativos import comparar_plataformas as _cmp_plat
            return _cmp_plat(data_inicio, data_fim, cliente_key=cliente_key)

        # Fallback: resumo geral
        return queries.resumo_por_campanha(data_inicio, data_fim, **_fk)

    def try_sql_fallback(
        self,
        pergunta: str,
        intent: str,
        cliente_key: str,
        data_inicio: Optional[str],
        data_fim: Optional[str],
    ) -> dict | None:
        """
        Tenta SQL dinâmico quando dados estão vazios e a pergunta tem filtros ad-hoc.

        Returns:
            dict com 'dados' e 'sql' se bem-sucedido, None caso contrário.
        """
        if intent not in ("desconhecida", "resumo_geral"):
            return None
        if intent == "resumo_geral" and not _ADOC_HINTS_RE.search(pergunta):
            return None

        logger.info("[AnalysisAgent] Intent '%s' ad-hoc — tentando SQL dinâmico", intent)
        try:
            from agent.sql_generator import executar_sql_dinamico
            result = executar_sql_dinamico(
                pergunta=pergunta,
                cliente_key=cliente_key,
                data_inicio=data_inicio,
                data_fim=data_fim,
            )
            if result["dados"] and not result["erro"]:
                logger.info(
                    "[AnalysisAgent] SQL dinâmico OK — %d linhas em %dms",
                    len(result["dados"]), result["latencia_ms"],
                )
                return result
            if result["erro"]:
                logger.warning("[AnalysisAgent] SQL dinâmico retornou erro: %s", result["erro"])
        except Exception as e:
            logger.warning("[AnalysisAgent] SQL dinâmico falhou: %s", e)
        return None

    def enrich_delta(
        self,
        dados: Any,
        intents: list[str],
        campanha: Optional[str],
        data_inicio: Optional[str],
        data_fim: Optional[str],
        cliente_key: Optional[str],
        plataforma: Optional[str],
    ) -> dict:
        """Calcula delta automático vs período anterior."""
        delta = {}
        if not (data_inicio and data_fim and not campanha):
            return delta

        if "resumo_geral" in intents and isinstance(dados, dict):
            # Delta preciso via queries.resumo_periodo
            try:
                from datetime import date as _date, timedelta as _td
                ini = _date.fromisoformat(data_inicio)
                fim = _date.fromisoformat(data_fim)
                delta_dias = (fim - ini).days + 1
                ant_fim = ini - _td(days=1)
                ant_ini = ant_fim - _td(days=delta_dias - 1)

                dados_ant = queries.resumo_periodo(
                    str(ant_ini), str(ant_fim),
                    cliente_key=cliente_key, plataforma=plataforma,
                )
                if dados_ant and dados_ant.get("investimento_total"):
                    def _pct(a, b):
                        return round((a - b) / b * 100, 1) if b else None

                    delta = {
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
            except Exception as e:
                logger.debug("[AnalysisAgent] delta resumo_geral falhou: %s", e)
        else:
            # Legado: outros intents via ads_diario
            try:
                import sqlite3 as _sql3
                from datetime import date as _ddate, timedelta as _dtd
                _d0 = _ddate.fromisoformat(data_inicio)
                _d1 = _ddate.fromisoformat(data_fim)
                _span = (_d1 - _d0).days + 1
                _ant0 = _d0 - _dtd(days=_span)
                _ant1 = _d1 - _dtd(days=_span)
                _c = get_conn()
                _c.row_factory = _sql3.Row
                _row = _c.execute(
                    "SELECT SUM(investimento) AS invest, "
                    "CASE WHEN SUM(investimento)>0 THEN SUM(valor_compras)/SUM(investimento) END AS roas, "
                    "SUM(compras) AS conversoes "
                    "FROM campanhas_unificada WHERE data_inicio BETWEEN ? AND ?",
                    (str(_ant0), str(_ant1)),
                ).fetchone()
                _invest_a = (
                    dados.get("invest") or dados.get("total_invest")
                    if isinstance(dados, dict)
                    else sum((r.get("investimento") or 0) for r in dados if isinstance(r, dict))
                    if isinstance(dados, list) else None
                )
                _roas_a = dados.get("roas") if isinstance(dados, dict) else None
                if _row and _row["invest"]:
                    def _pct_l(a, b): return ((a - b) / b * 100) if b else None
                    if _invest_a and _row["invest"]:
                        delta["invest"] = (_invest_a, _row["invest"], _pct_l(_invest_a, _row["invest"]))
                    if _roas_a and _row["roas"]:
                        delta["roas"] = (_roas_a, _row["roas"], _pct_l(_roas_a, _row["roas"]))
            except Exception as e:
                logger.debug("[AnalysisAgent] delta legado falhou: %s", e)

        return delta

    def enrich_shopify(
        self,
        dados: Any,
        data_inicio: Optional[str],
        data_fim: Optional[str],
    ) -> tuple[Any, dict]:
        """
        Enriquece dados com receita Shopify real.

        Returns:
            (dados_enriquecidos, shopify_dados)
        """
        shopify = {}
        if not (data_inicio and data_fim):
            return dados, shopify
        try:
            _sd = queries.receita_shopify_periodo(data_inicio, data_fim)
            if _sd.get("sincronizado"):
                shopify = _sd
                roas_real = _sd.get("roas_real")
                if roas_real and isinstance(dados, dict):
                    dados["roas_shopify"]           = roas_real
                    dados["receita_shopify"]        = _sd.get("receita_total")
                    dados["cac"]                    = _sd.get("cac")
                    dados["novos_clientes_shopify"] = _sd.get("novos_clientes")
                    dados["ticket_medio_shopify"]   = _sd.get("ticket_medio")
                    dados["taxa_recompra_pct"]      = _sd.get("taxa_recompra_pct")
                logger.debug(
                    "[AnalysisAgent] Shopify enriquecido: receita=%.2f roas_real=%s",
                    _sd.get("receita_total", 0), _sd.get("roas_real"),
                )
        except Exception as e:
            logger.debug("[AnalysisAgent] Shopify não disponível: %s", e)
        return dados, shopify

    def execute_for_segment(
        self,
        intent: str,
        params: dict,
        segmentos: list[dict],
        meta_extra: dict = None,
    ) -> list[dict]:
        """
        Executa intent para cada segmento (cliente + plataforma) separadamente.
        Retorna lista de resultados por segmento.
        """
        campanha    = params.get("campanha")
        data_inicio = params.get("data_inicio")
        data_fim    = params.get("data_fim")
        limit       = params.get("limit", 10)

        resultados = []
        for seg in segmentos:
            ck   = seg["cliente_key"]
            plat = seg["plataforma"]
            try:
                dados = self.execute_intent(
                    intent, campanha, data_inicio, data_fim, limit, meta_extra,
                    cliente_key=ck, plataforma=plat,
                )
                vazio = (
                    (isinstance(dados, list) and len(dados) == 0)
                    or (isinstance(dados, dict) and not dados)
                    or dados is None
                )
                resultados.append({**seg, "dados": dados, "vazio": vazio})
            except Exception as e:
                logger.error("[AnalysisAgent] Erro no segmento %s: %s", seg.get("label"), e)
                resultados.append({**seg, "dados": None, "vazio": True, "erro": str(e)})

        return resultados
