"""
transcricao/agents/

Pipeline de subagentes do Subagente de Transcrição, um por Camada do
fluxograma documentado em "Metodologia aplicada — Subagente de
Transcrição" (Notion):

  IngestaoAgent    — Camada 0 (Fundamento)
  ParserAgent      — Camada 1 (Interpretação)        [já testado: ../tests/]
  AnaliseAgent     — Camada 2 (Análise/Claude)
  TicketAgent      — Camada 3 (Regra de negócio: tickets/SLA)
  MetricasAgent    — Camada 3 (Regra de negócio: métricas/IC Wilson)
  ComunicacaoAgent — Camada 4 (Comunicação: cards Notion)

Cada classe aqui é uma camada fina sobre as funções já existentes nos
scripts legados (sync_reunioes.py, analisador_reunioes.py,
gestor_alertas.py, metricas_alertas.py, criar_tarefas_notion.py) — não
reimplementa nenhuma lógica de negócio, só organiza o fluxo que hoje
vive em cada main() numa forma reutilizável e testável isoladamente.
Os scripts legados continuam sendo os entrypoints de CLI/cron; agora
delegam a orquestração pra essas classes.
"""
from .analise import AnaliseAgent
from .base import BaseSubagente
from .comunicacao import ComunicacaoAgent
from .ingestao import IngestaoAgent
from .metricas import MetricasAgent
from .parser import ParserAgent
from .ticket import TicketAgent

PIPELINE = [IngestaoAgent, ParserAgent, AnaliseAgent, TicketAgent, MetricasAgent, ComunicacaoAgent]

__all__ = [
    "BaseSubagente",
    "IngestaoAgent",
    "ParserAgent",
    "AnaliseAgent",
    "TicketAgent",
    "MetricasAgent",
    "ComunicacaoAgent",
    "PIPELINE",
]
