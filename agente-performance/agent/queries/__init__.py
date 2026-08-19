"""
agent/queries/__init__.py

Re-exporta todas as funções públicas dos submódulos para compatibilidade
com imports existentes do tipo:
    from agent.queries import resumo_periodo, top_campanhas_roas, ...

Estrutura de queries:
    shared/            — queries agnósticas (v_performance + filtro cliente_key)
    source_of/         — atalhos Source OF por plataforma
      meta_ads.py      — Source OF · Meta Ads
      google_ads.py    — Source OF · Google Ads
      shopify.py       — Source OF · Shopify (exclusivo)
    pod_kombucha/      — atalhos POD Kombucha por plataforma
      meta_ads.py      — POD · Meta Ads
      google_ads.py    — POD · Google Ads (⏳ aguardando customer_id)

Submódulos compartilhados (fonte de verdade):
    base.py            — helpers internos + diagnose
    resumo.py          — resumos agregados
    ranking.py         — rankings de campanhas
    alertas.py         — alertas de ROAS e conversão
    evolucao.py        — evolução diária e comparativos
    criativos.py       — criativos e engajamento
    shopify.py         — receita Shopify
    comparativos.py    — comparativos entre períodos e campanhas
"""

from agent.queries.base import data_range_disponivel, listar_campanhas, verificar_gap_dados
from agent.queries.resumo import resumo_por_campanha, performance_campanha, resumo_periodo, resumo_executivo, historico_mensal, metricas_funil, custo_funil_detalhado, distribuicao_investimento, projecao_fechamento_mes
from agent.queries.ranking import top_campanhas_roas, top_campanhas_compras, top_campanhas_ctr, top_campanhas_cpm, top_campanhas_alcance, top_campanhas_cliques, top_campanhas_receita, eficiencia_criativo
from agent.queries.alertas import alertas_roas_baixo, campanhas_sem_conversao, campanhas_inativas, saturacao_frequencia
from agent.queries.evolucao import evolucao_diaria, variacao_diaria_roas, comparar_campanhas, comparar_mesmo_periodo_ano_anterior
from agent.queries.criativos import top_criativos_roas, criativos_em_fadiga, metricas_engajamento, metricas_mensageria, metricas_video
from agent.queries.shopify import receita_shopify_periodo, metricas_shopify_diario, status_shopify_sync
from agent.queries.comparativos import (
    METRICAS_DISPONIVEIS,
    comparar_periodos,
    comparar_campanhas_detalhado,
    comparar_semana_a_semana,
    comparar_mes_a_mes,
    comparar_antes_depois,
    ranking_comparativo,
    comparar_plataformas,
)

__all__ = ['METRICAS_DISPONIVEIS', 'alertas_roas_baixo', 'campanhas_inativas', 'campanhas_sem_conversao', 'comparar_antes_depois', 'comparar_campanhas', 'comparar_campanhas_detalhado', 'comparar_mes_a_mes', 'comparar_mesmo_periodo_ano_anterior', 'comparar_periodos', 'comparar_plataformas', 'comparar_semana_a_semana', 'criativos_em_fadiga', 'custo_funil_detalhado', 'data_range_disponivel', 'distribuicao_investimento', 'eficiencia_criativo', 'evolucao_diaria', 'historico_mensal', 'listar_campanhas', 'metricas_engajamento', 'metricas_funil', 'metricas_mensageria', 'metricas_shopify_diario', 'metricas_video', 'performance_campanha', 'projecao_fechamento_mes', 'ranking_comparativo', 'receita_shopify_periodo', 'resumo_executivo', 'resumo_periodo', 'resumo_por_campanha', 'saturacao_frequencia', 'status_shopify_sync', 'top_campanhas_alcance', 'top_campanhas_cliques', 'top_campanhas_compras', 'top_campanhas_cpm', 'top_campanhas_ctr', 'top_campanhas_receita', 'top_campanhas_roas', 'top_criativos_roas', 'variacao_diaria_roas', 'verificar_gap_dados']
