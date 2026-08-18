"""
agent/queries/pod_kombucha/__init__.py

Queries específicas do cliente POD Kombucha.

Plataformas ativas:
  - Meta Ads    (pod_meta.pod_kombucha_meta_ads_diario)  ✅ ativo
  - Google Ads  (pod_gads.pod_kombucha_gads_diario)      ⏳ ativo no DB, aguardando customer_id

Shopify: ❌ não integrado

Uso:
    from agent.queries.pod_kombucha import resumo_meta, top_roas
    from agent.queries.pod_kombucha.meta_ads import evolucao
"""
from agent.queries.pod_kombucha.meta_ads   import *  # noqa: F401,F403
from agent.queries.pod_kombucha.google_ads import *  # noqa: F401,F403
