"""
clientes/pod/relatorio/diario.py

⚠️  ALIAS — mantido para compatibilidade.
A implementação está em agent/clients/relatorio/pod.py (PODRelatorioAgent).

Uso preferencial:
    from agent.clients.relatorio import get_relatorio_agent
    get_relatorio_agent("pod_kombucha").gerar_diario(data="2026-08-18")
"""
from agent.clients.relatorio.pod import PODRelatorioAgent as _A

def gerar(data: str = None, plataforma: str = "meta_ads", publicar: bool = True) -> dict:
    return _A().gerar_diario(data=data, plataforma=plataforma, publicar=publicar)
