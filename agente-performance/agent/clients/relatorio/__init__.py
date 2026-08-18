"""
agent/clients/relatorio/__init__.py

Subagentes de relatório por cliente.

Factory:
    get_relatorio_agent(cliente_key) → BaseRelatorioAgent
"""
from agent.clients.relatorio.base import BaseRelatorioAgent
from agent.clients.relatorio.source_of import SourceOFRelatorioAgent
from agent.clients.relatorio.pod import PODRelatorioAgent


def get_relatorio_agent(cliente_key: str | None) -> "BaseRelatorioAgent":
    """
    Factory: retorna o agente de relatório correto para o cliente.

    Args:
        cliente_key: 'source_of' | 'pod_kombucha'

    Returns:
        Agente de relatório do cliente.
    """
    if cliente_key == "source_of":
        return SourceOFRelatorioAgent()
    if cliente_key == "pod_kombucha":
        return PODRelatorioAgent()
    raise ValueError(f"cliente_key desconhecido para relatório: {cliente_key!r}")


__all__ = [
    "BaseRelatorioAgent",
    "SourceOFRelatorioAgent",
    "PODRelatorioAgent",
    "get_relatorio_agent",
]
