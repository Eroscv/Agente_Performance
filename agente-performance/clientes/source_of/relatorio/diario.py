"""
clientes/source_of/relatorio/diario.py

⚠️  ALIAS — mantido para compatibilidade.
A implementação está em agent/clients/relatorio/source_of.py (SourceOFRelatorioAgent).

Uso preferencial:
    from agent.clients.relatorio import get_relatorio_agent
    get_relatorio_agent("source_of").gerar_diario(data="2026-08-18")
"""
from agent.clients.relatorio.source_of import SourceOFRelatorioAgent as _A

def gerar(data: str = None, plataforma: str = None, publicar: bool = True) -> dict:
    return _A().gerar_diario(data=data, plataforma=plataforma, publicar=publicar)
