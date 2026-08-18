"""
clientes/pod/insights.py

Insights automáticos exclusivos do POD Kombucha.
Wrapper que delega para relatorio/insights.py com cliente_key fixo.
"""
from relatorio.insights import gerar_insights as _gerar


def gerar(
    resumo: dict,
    campanhas: list,
    periodo: str = "diario",
    plataforma: str = "google_ads",
    periodo_str: str = "",
    dados_extras: dict = None,
) -> list:
    """
    Gera blocos Notion com insights do POD Kombucha.

    Args:
        resumo:      dict com métricas consolidadas do período
        campanhas:   lista de dicts com dados por campanha
        periodo:     'diario' | 'semanal' | 'mensal'
        plataforma:  'meta_ads' | 'google_ads' (default: google_ads)
        periodo_str: string descritiva do período
        dados_extras: dados adicionais (comparativo, etc.)

    Returns:
        Lista de blocos Notion
    """
    return _gerar(
        resumo=resumo,
        campanhas=campanhas,
        periodo=periodo,
        cliente_key="pod_kombucha",
        plataforma=plataforma,
        periodo_str=periodo_str,
        dados_extras=dados_extras,
    )
