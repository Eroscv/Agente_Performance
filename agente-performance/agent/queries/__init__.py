"""
agent/queries/__init__.py

Re-exporta todas as funções públicas dos submódulos para compatibilidade
com imports existentes do tipo:
    from agent.queries import resumo_periodo, top_campanhas_roas, ...

Submódulos:
    base.py       — helpers internos e diagnóstico
    resumo.py     — resumos agregados
    ranking.py    — rankings de campanhas
    alertas.py    — alertas de ROAS e conversão
    evolucao.py   — evolução diária e comparativos
    criativos.py  — criativos e engajamento
    shopify.py    — receita Shopify
"""

from agent.queries.base import data_range_disponivel, listar_campanhas, verificar_gap_dados
from agent.queries.resumo import resumo_por_campanha, performance_campanha, resumo_periodo, resumo_executivo, historico_mensal, metricas_funil, custo_funil_detalhado, distribuicao_investimento, projecao_fechamento_mes
from agent.queries.ranking import top_campanhas_roas, top_campanhas_compras, top_campanhas_ctr, top_campanhas_cpm, top_campanhas_alcance, top_campanhas_cliques, top_campanhas_receita, eficiencia_criativo
from agent.queries.alertas import alertas_roas_baixo, campanhas_sem_conversao, campanhas_inativas, saturacao_frequencia
from agent.queries.evolucao import evolucao_diaria, variacao_diaria_roas, comparar_campanhas, comparar_mesmo_periodo_ano_anterior
from agent.queries.criativos import top_criativos_roas, criativos_em_fadiga, metricas_engajamento, metricas_mensageria, metricas_video
from agent.queries.shopify import receita_shopify_periodo, metricas_shopify_diario, status_shopify_sync

__all__ = ['alertas_roas_baixo', 'campanhas_inativas', 'campanhas_sem_conversao', 'comparar_campanhas', 'comparar_mesmo_periodo_ano_anterior', 'criativos_em_fadiga', 'custo_funil_detalhado', 'data_range_disponivel', 'distribuicao_investimento', 'eficiencia_criativo', 'evolucao_diaria', 'historico_mensal', 'listar_campanhas', 'metricas_engajamento', 'metricas_funil', 'metricas_mensageria', 'metricas_shopify_diario', 'metricas_video', 'performance_campanha', 'projecao_fechamento_mes', 'receita_shopify_periodo', 'resumo_executivo', 'resumo_periodo', 'resumo_por_campanha', 'saturacao_frequencia', 'status_shopify_sync', 'top_campanhas_alcance', 'top_campanhas_cliques', 'top_campanhas_compras', 'top_campanhas_cpm', 'top_campanhas_ctr', 'top_campanhas_receita', 'top_campanhas_roas', 'top_criativos_roas', 'variacao_diaria_roas', 'verificar_gap_dados']
