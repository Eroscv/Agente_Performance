"""
agent/queries/shared/__init__.py

Queries agnósticas por cliente — filtram via cliente_key/plataforma em v_performance.
Re-exporta todos os módulos compartilhados para acesso direto.

Uso:
    from agent.queries.shared import resumo_por_campanha, top_campanhas_roas
    from agent.queries.shared.resumo import resumo_executivo
"""
from agent.queries.resumo      import *  # noqa: F401,F403
from agent.queries.ranking     import *  # noqa: F401,F403
from agent.queries.alertas     import *  # noqa: F401,F403
from agent.queries.evolucao    import *  # noqa: F401,F403
from agent.queries.criativos   import *  # noqa: F401,F403
from agent.queries.comparativos import *  # noqa: F401,F403
