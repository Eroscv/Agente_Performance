"""
db/shopify_sync.py

⚠️  ALIAS — mantido para compatibilidade com imports existentes.
A implementação foi movida para shopify/sync.py.

Novos módulos devem importar diretamente de shopify.sync.
"""
from shopify.sync import (  # noqa: F401
    sincronizar,
    receita_por_dia,
    receita_periodo,
    metricas_shopify_periodo,
    roas_real_periodo,
    status_sync,
    carregar_shopify,
)
