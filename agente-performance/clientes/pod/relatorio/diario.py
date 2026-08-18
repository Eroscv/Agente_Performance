"""
clientes/pod/relatorio/diario.py

Relatório diário do POD Kombucha.
Wrapper que delega para relatorio/builder_diario.py com cliente_key fixo.
"""
from relatorio.builder_diario import gerar_relatorio_diario as _gerar


def gerar(data: str = None, plataforma: str = "meta_ads", publicar: bool = True) -> dict:
    """
    Gera e publica o relatório diário do POD Kombucha.

    Args:
        data:       Data no formato YYYY-MM-DD (default: ontem)
        plataforma: 'meta_ads' | 'google_ads'
        publicar:   Se True, publica no Notion e registra no log

    Returns:
        dict com chaves: titulo, resumo, blocos, campanhas, data, notion_url
    """
    return _gerar(
        cliente_key="pod_kombucha",
        plataforma=plataforma,
        data=data,
        publicar=publicar,
    )
