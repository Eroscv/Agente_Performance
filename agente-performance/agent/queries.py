"""
agent/queries.py

⚠️  ARQUIVO LEGADO — mantido para compatibilidade de imports existentes.

A implementação foi dividida em submódulos em agent/queries/:
    base.py       — helpers internos e diagnóstico
    resumo.py     — resumos agregados por campanha e período
    ranking.py    — rankings por ROAS, compras, CTR, CPM...
    alertas.py    — alertas de ROAS baixo, sem conversão, fadiga
    evolucao.py   — evolução diária e comparativos de período
    criativos.py  — criativos, engajamento, vídeo
    shopify.py    — receita Shopify sincronizada

Para novos imports, prefira importar do submódulo diretamente:
    from agent.queries.ranking import top_campanhas_roas
    from agent.queries.alertas import alertas_roas_baixo

Imports deste arquivo continuam funcionando via re-export do __init__:
    from agent.queries import top_campanhas_roas  # ainda funciona
"""
from agent.queries import *  # noqa: F401, F403 — re-export total para compatibilidade
