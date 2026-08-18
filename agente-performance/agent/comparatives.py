"""
agent/comparatives.py

⚠️  ALIAS — mantido para compatibilidade com imports existentes.
A implementação foi movida para agent/queries/comparativos.py.

Novos módulos devem importar diretamente de agent.queries.comparativos.
"""
from agent.queries.comparativos import (  # noqa: F401
    METRICAS_DISPONIVEIS,
    comparar_periodos,
    comparar_campanhas_detalhado,
    comparar_semana_a_semana,
    comparar_mes_a_mes,
    comparar_antes_depois,
    ranking_comparativo,
)
