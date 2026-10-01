"""
agent/clients/__init__.py

Pacote de agentes por cliente.

Exporta a factory get_client_agent(cliente_key) que retorna o agente
correto para cada cliente. O OrchestratorAgent usa esta factory para
despachar para o subagente certo sem conhecer os detalhes de cada cliente.
"""
from agent.clients.base import BaseClientAgent
from agent.clients.pod import PODAgent
from agent.clients.source_of import SourceOFAgent
from agent.clients.botica import BoticaAgent
from agent.clients.general import GeneralAgent


def get_client_agent(cliente_key: str | None) -> "BaseClientAgent":
    """
    Factory: retorna o agente correto para o cliente.

    Args:
        cliente_key: 'source_of' | 'pod_kombucha' | 'botica' | None

    Returns:
        Instância do agente cliente correspondente.
        None / desconhecido → GeneralAgent (multi-cliente).
    """
    if cliente_key == "source_of":
        return SourceOFAgent()
    if cliente_key == "pod_kombucha":
        return PODAgent()
    if cliente_key == "botica":
        return BoticaAgent()
    return GeneralAgent()


__all__ = [
    "BaseClientAgent",
    "PODAgent",
    "SourceOFAgent",
    "BoticaAgent",
    "GeneralAgent",
    "get_client_agent",
]
