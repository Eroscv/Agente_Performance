"""
clientes/botica/insights.py

Insights automáticos da Botica Ouro Preto.
Wrapper que delega para relatorio/insights.py com cliente_key fixo.
"""
from relatorio.insights import gerar_insights as _gerar


def gerar(
    resumo: dict,
    campanhas: list,
    periodo: str = "diario",
    plataforma: str = "meta_ads",
    periodo_str: str = "",
    dados_extras: dict = None,
) -> list:
    """
    Gera blocos Notion com insights da Botica Ouro Preto.

    Args:
        resumo:      dict com métricas consolidadas do período
        campanhas:   lista de dicts com dados por campanha
        periodo:     'diario' | 'semanal' | 'mensal'
        plataforma:  'meta_ads' (único por ora)
        periodo_str: string descritiva do período
        dados_extras: dados adicionais

    Returns:
        Lista de blocos Notion
    """
    return _gerar(
        resumo=resumo,
        campanhas=campanhas,
        periodo=periodo,
        plataforma=plataforma,
        periodo_str=periodo_str,
        dados_extras=dados_extras,
        cliente_key="botica",
    )
