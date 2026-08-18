"""
clientes/source_of/relatorio/mensal.py

Relatório mensal da Source OF (Meta Ads).
Wrapper que delega para relatorio/builder_mensal.py com cliente fixo.
"""
from relatorio.builder_mensal import gerar_relatorio_mensal as _gerar


def gerar(
    ano: int = None,
    mes: int = None,
    publicar: bool = True,
) -> dict:
    """
    Gera e publica o relatório mensal da Source OF (Meta Ads).

    Args:
        ano:      Ano do mês de referência (default: mês passado)
        mes:      Mês de referência 1-12 (default: mês passado)
        publicar: Se True, publica no Notion

    Returns:
        dict com chaves: page_id, url, resumo, num_campanhas, alertas
    """
    return _gerar(
        cliente="source_of",
        ano=ano,
        mes=mes,
        publicar=publicar,
    )
