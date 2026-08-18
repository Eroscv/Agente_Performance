"""
clientes/pod/relatorio/mensal.py

Relatório mensal do POD Kombucha.
Wrapper que delega para relatorio/builder_pod.py.
"""
from datetime import date
from relatorio.builder_pod import gerar_relatorio_mensal_pod as _gerar


def gerar(
    data_inicio: date = None,
    data_fim: date = None,
    publicar: bool = True,
) -> dict:
    """
    Gera e publica o relatório mensal do POD Kombucha (Google Ads).

    Args:
        data_inicio: Início do mês (default: primeiro dia do mês atual)
        data_fim:    Fim do mês (default: último dia do mês atual)
        publicar:    Se True, publica no Notion

    Returns:
        dict com chaves: titulo, blocos, resumo, notion_url
    """
    return _gerar(data_inicio=data_inicio, data_fim=data_fim, publicar=publicar)
