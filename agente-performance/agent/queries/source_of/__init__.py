"""
agent/queries/source_of/__init__.py

Queries específicas do cliente Source OF.

Plataformas ativas:
  - Meta Ads    (sof_meta.source_of_meta_ads_diario)
  - Google Ads  (sof_gads.source_of_gads_diario)
  - Shopify     (shopify_src.shopify_receita_diaria)

Uso:
    from agent.queries.source_of import resumo_meta, resumo_shopify
    from agent.queries.source_of.shopify import receita_shopify_periodo
"""
from agent.queries.source_of.meta_ads   import *  # noqa: F401,F403
from agent.queries.source_of.google_ads import *  # noqa: F401,F403
from agent.queries.source_of.shopify    import *  # noqa: F401,F403
