"""
clientes/source_of/relatorio/semanal.py

⚠️  ALIAS — mantido para compatibilidade.
A implementação está em agent/clients/relatorio/source_of.py (SourceOFRelatorioAgent).

Uso preferencial:
    from agent.clients.relatorio import get_relatorio_agent
    get_relatorio_agent("source_of").gerar_semanal()
"""
from datetime import date
from agent.clients.relatorio.source_of import SourceOFRelatorioAgent as _A

def gerar(data_inicio: date = None, data_fim: date = None, publicar: bool = True) -> dict:
    return _A().gerar_semanal(data_inicio=data_inicio, data_fim=data_fim, publicar=publicar)
