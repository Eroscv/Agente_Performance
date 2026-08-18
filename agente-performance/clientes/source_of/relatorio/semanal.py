"""
clientes/source_of/relatorio/semanal.py

Relatório semanal da Source OF (Meta Ads).
Wrapper que delega para relatorio/builder_semanal.py com cliente fixo.
"""
from datetime import date
from pathlib import Path
from relatorio.builder_semanal import gerar_relatorio_semanal as _gerar


def gerar(
    data_inicio: date = None,
    data_fim: date = None,
    publicar: bool = True,
) -> dict:
    """
    Gera e publica o relatório semanal da Source OF (Meta Ads).

    Args:
        data_inicio: Início do período (default: segunda-feira da semana passada)
        data_fim:    Fim do período (default: domingo da semana passada)
        publicar:    Se True, publica no Notion

    Returns:
        dict com chaves: page_id, url, resumo, num_campanhas, alertas
    """
    return _gerar(
        cliente="source_of",
        data_inicio=data_inicio,
        data_fim=data_fim,
        publicar=publicar,
    )
