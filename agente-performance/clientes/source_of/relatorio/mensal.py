"""
clientes/source_of/relatorio/mensal.py

⚠️  ALIAS — mantido para compatibilidade.
A implementação está em agent/clients/relatorio/source_of.py (SourceOFRelatorioAgent).

Uso preferencial:
    from agent.clients.relatorio import get_relatorio_agent
    get_relatorio_agent("source_of").gerar_mensal(ano=2026, mes=7)
"""
from agent.clients.relatorio.source_of import SourceOFRelatorioAgent as _A

def gerar(ano: int = None, mes: int = None, publicar: bool = True) -> dict:
    return _A().gerar_mensal(ano=ano, mes=mes, publicar=publicar)
