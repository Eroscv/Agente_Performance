"""
agent/queries/source_of/meta_ads.py

Queries específicas Source OF · Meta Ads.
Atalhos com cliente_key e plataforma pré-fixados.
"""
from __future__ import annotations
from typing import Optional

_CK   = "source_of"
_PLAT = "meta_ads"


def resumo(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Resumo por campanha — Source OF · Meta Ads."""
    from agent.queries.resumo import resumo_por_campanha
    return resumo_por_campanha(data_inicio, data_fim,
                               cliente_key=_CK, plataforma=_PLAT)


def resumo_periodo(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> dict:
    """Resumo consolidado do período — Source OF · Meta Ads."""
    from agent.queries.resumo import resumo_periodo as _fn
    return _fn(data_inicio, data_fim, cliente_key=_CK, plataforma=_PLAT)


def top_roas(
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    limit:       int = 10,
) -> list[dict]:
    """Top campanhas por ROAS — Source OF · Meta Ads."""
    from agent.queries.ranking import top_campanhas_roas
    return top_campanhas_roas(limit=limit, data_inicio=data_inicio,
                              data_fim=data_fim,
                              cliente_key=_CK, plataforma=_PLAT)


def alertas(
    roas_minimo: float = 1.0,
    janela_dias: int = 7,
) -> list[dict]:
    """Alertas de ROAS baixo — Source OF · Meta Ads."""
    from agent.queries.alertas import alertas_roas_baixo
    return alertas_roas_baixo(threshold=roas_minimo, janela_dias=janela_dias,
                              cliente_key=_CK, plataforma=_PLAT)


def criativos(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
    limit:       int = 10,
) -> list[dict]:
    """Top criativos por ROAS — Source OF · Meta Ads (ad-level)."""
    from agent.queries.criativos import top_criativos_roas
    return top_criativos_roas(campanha=campanha, data_inicio=data_inicio,
                              data_fim=data_fim, limit=limit, cliente_key=_CK)


def evolucao(
    campanha:    Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim:    Optional[str] = None,
) -> list[dict]:
    """Evolução diária — Source OF · Meta Ads."""
    from agent.queries.evolucao import evolucao_diaria
    return evolucao_diaria(campanha=campanha, data_inicio=data_inicio,
                           data_fim=data_fim,
                           cliente_key=_CK, plataforma=_PLAT)
