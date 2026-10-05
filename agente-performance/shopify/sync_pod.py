"""
shopify/sync_pod.py

⚠️  ALIAS — mantido para compatibilidade com imports existentes.
A implementação foi migrada para shopify/sync.py com suporte multi-cliente.

Novos módulos devem usar:
    from shopify.sync import sincronizar, status_sync, carregar_shopify
    sincronizar(data_inicio, data_fim, cliente_key="pod_kombucha")
"""
from __future__ import annotations

from datetime import date, timedelta
from shopify.sync import (
    sincronizar as _sincronizar,
    status_sync as _status_sync,
    carregar_shopify as _carregar_shopify,
)

_CLIENTE = "pod_kombucha"


def sincronizar(
    data_inicio: date = None,
    data_fim: date = None,
    dias: int = 8,
    dry_run: bool = False,
) -> dict:
    """Alias — delega para shopify.sync.sincronizar(cliente_key='pod_kombucha')."""
    if data_fim is None:
        data_fim = date.today()
    if data_inicio is None:
        data_inicio = data_fim - timedelta(days=dias - 1)
    return _sincronizar(data_inicio, data_fim, dry_run=dry_run, cliente_key=_CLIENTE)


def status_sync() -> dict:
    """Alias — delega para shopify.sync.status_sync(cliente_key='pod_kombucha')."""
    return _status_sync(cliente_key=_CLIENTE)


def carregar_shopify(dias: int = 8) -> dict:
    """Alias — delega para shopify.sync.carregar_shopify(cliente_key='pod_kombucha')."""
    return _carregar_shopify(dias=dias, cliente_key=_CLIENTE)
