"""
clientes/pod/relatorio/semanal.py

⚠️  ALIAS — mantido para compatibilidade.
A implementação está em agent/clients/relatorio/pod.py (PODRelatorioAgent).

Uso preferencial:
    from agent.clients.relatorio import get_relatorio_agent
    get_relatorio_agent("pod_kombucha").gerar_semanal()
"""
from datetime import date
from agent.clients.relatorio.pod import PODRelatorioAgent as _A

def gerar(data_inicio: date = None, data_fim: date = None, publicar: bool = True) -> dict:
    return _A().gerar_semanal(data_inicio=data_inicio, data_fim=data_fim, publicar=publicar)
