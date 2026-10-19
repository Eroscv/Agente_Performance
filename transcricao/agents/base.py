"""
transcricao/agents/base.py

Contrato comum dos subagentes do pipeline de Transcrição.
"""
from abc import ABC, abstractmethod


class BaseSubagente(ABC):
    """Cada subagente mapeia 1:1 pra uma Camada do fluxograma documentado
    em "Metodologia aplicada — Subagente de Transcrição" (Notion):

        IngestaoAgent    — Camada 0 (Fundamento)
        ParserAgent      — Camada 1 (Interpretação)
        AnaliseAgent     — Camada 2 (Análise/Claude)
        TicketAgent      — Camada 3 (Regra de negócio: tickets/SLA)
        MetricasAgent    — Camada 3 (Regra de negócio: métricas/IC Wilson)
        ComunicacaoAgent — Camada 4 (Comunicação)

    executar() é o ponto de entrada único de cada subagente — mesmo papel
    que BaseClientAgent.execute() tem em
    agente-performance/agent/clients/__init__.py, só que aqui a
    especialização é por ESTÁGIO do pipeline, não por cliente (este
    subagente não varia por cliente).
    """

    CAMADA: str = ""  # ex: "0 — Ingestão" — preenchido por subclasse

    @abstractmethod
    def executar(self, **kwargs) -> dict:
        """Executa o trabalho desta camada e devolve um resumo (contadores)."""
        raise NotImplementedError
