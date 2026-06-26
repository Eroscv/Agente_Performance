"""
relatorio/

Agente de Relatoria — gera relatórios padronizados.

Módulos disponíveis:
  - regras.py   → constantes e funções puras de cálculo (fator imposto, ROAS, CAC, CPA…)
  - meta.py     → blocos de dados para relatórios Meta Ads
  - template.py → blocos Notion por seção (cover, métricas, tabelas, funil)  [TODO]
  - builder.py  → orquestra dados + template → payload Notion completo        [TODO]
  - uploader.py → upload de assets (banner) via URL externa                   [TODO]
  - agent.py    → ponto de entrada — decide tipo, busca dados, publica        [TODO]
  - scheduler.py→ agendamento automático (semanal, mensal)                   [TODO]

Regras de negócio Meta Ads (reunião Bianca/Eros):
  - Fator de imposto: 1,1215
  - ROAS = Receita Shopify / Invest. c/ Imposto  (NÃO usa ROAS do Meta)
  - CAC  = Invest. c/ Imposto / Novos Clientes   (Shopify via UTM)
  - CPA  = Invest. c/ Imposto / Compras          (NÃO usa CPA do Meta)
  - CPC  → direto da plataforma, NÃO recalcular
  - Ticket Médio = Receita / Transações
"""

from relatorio.regras import (
    FATOR_IMPOSTO,
    spend_com_imposto,
    calcular_roas,
    calcular_cac,
    calcular_cpa,
    calcular_ticket_medio,
    calcular_pct_budget,
)

from relatorio.meta import (
    tabela_principal,
    alocacao_budget,
    top_ads,
)

__all__ = [
    # regras
    "FATOR_IMPOSTO",
    "spend_com_imposto",
    "calcular_roas",
    "calcular_cac",
    "calcular_cpa",
    "calcular_ticket_medio",
    "calcular_pct_budget",
    # meta
    "tabela_principal",
    "alocacao_budget",
    "top_ads",
]
