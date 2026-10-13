"""
agent/orchestrator/__init__.py

Pacote OrchestratorAgent — arquitetura multi-agente (Fase 1).

STATUS: implementado, não ativado em produção.
        Produção usa agent/engine.py → consultar() diretamente.

Para ativar: definir MULTI_AGENT=1 no .env
             e trocar consultar() → consultar_v2() em slack/handlers.py

Estrutura:
  agent.py   → OrchestratorAgent: coordena NLP + roteamento + queries
"""
from agent.orchestrator.agent import OrchestratorAgent

__all__ = ["OrchestratorAgent"]
