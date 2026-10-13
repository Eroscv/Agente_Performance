"""
agent/orchestrator_agent.py

⚠️  ARQUIVO LEGADO — mantido apenas para compatibilidade de imports externos.

A implementação real foi movida para:
    agent/orchestrator/agent.py
    agent/orchestrator/__init__.py

Use:
    from agent.orchestrator import OrchestratorAgent
"""
from agent.orchestrator import OrchestratorAgent  # noqa: F401 — re-export

__all__ = ["OrchestratorAgent"]
