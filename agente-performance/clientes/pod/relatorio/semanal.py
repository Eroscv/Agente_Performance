"""
clientes/pod/relatorio/semanal.py

Relatório semanal do POD Kombucha.
Wrapper que delega para relatorio/builder_pod.py.
"""
from datetime import date
from relatorio.builder_pod import gerar_relatorio_semanal_pod as _gerar


def gerar(
    data_inicio: date = None,
    data_fim: date = None,
    publicar: bool = True,
) -> dict:
    """
    Gera e publica o relatório semanal do POD Kombucha (Google Ads).

    Args:
        data_inicio: Início do período (default: última segunda-feira)
        data_fim:    Fim do período (default: último domingo)
        publicar:    Se True, publica no Notion

    Returns:
        dict com chaves: titulo, blocos, resumo, notion_url
    """
    return _gerar(data_inicio=data_inicio, data_fim=data_fim, publicar=publicar)
