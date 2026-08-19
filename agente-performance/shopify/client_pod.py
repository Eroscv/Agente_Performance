"""
shopify/client_pod.py

Cliente Shopify para POD Kombucha.
Mesma estrutura do client.py (Source OF) — só muda store/token.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Iterator

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_STORE   = os.getenv("SHOPIFY_POD_STORE", "d5bead-1b")
_TOKEN   = os.getenv("SHOPIFY_POD_TOKEN", "")
_API_VER = "2024-01"
_BASE    = f"https://{_STORE}.myshopify.com/admin/api/{_API_VER}"
_HEADERS = {"X-Shopify-Access-Token": _TOKEN, "Content-Type": "application/json"}

_ORDER_FIELDS = (
    "id,created_at,total_price,subtotal_price,total_discounts,"
    "financial_status,fulfillment_status,"
    "line_items,billing_address,shipping_address,customer"
)
_PAGE_SIZE   = 250
_RETRY_AFTER = 2.0


def _get(endpoint: str, params: dict = None) -> dict:
    url = f"{_BASE}/{endpoint}"
    while True:
        resp = requests.get(url, headers=_HEADERS, params=params or {}, timeout=30)
        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", _RETRY_AFTER))
            logger.warning("[pod_shopify] Rate limit — aguardando %.1fs", wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()


def _paginar_pedidos(data_inicio: date, data_fim: date) -> Iterator[dict]:
    """Itera sobre todos os pedidos do período via cursor-based pagination."""
    params = {
        "status":         "any",
        "created_at_min": f"{data_inicio}T00:00:00-03:00",
        "created_at_max": f"{data_fim}T23:59:59-03:00",
        "fields":         _ORDER_FIELDS,
        "limit":          _PAGE_SIZE,
    }
    while True:
        data = _get("orders.json", params)
        pedidos = data.get("orders", [])
        yield from pedidos
        if len(pedidos) < _PAGE_SIZE:
            break
        params = {**params, "since_id": pedidos[-1]["id"]}


def _orders_count_por_cliente(customer_ids: set, data_inicio: date) -> dict:
    """
    Retorna contagem de pedidos históricos por customer_id para detectar recompras.
    Usa /orders.json com filtro de data em vez de 1 request por cliente (muito mais eficiente).
    """
    if not customer_ids:
        return {}
    # Busca todos os pedidos ANTES do período para detectar recompra
    # Mais eficiente: 1 request paginado vs N requests individuais
    contagem: dict = {cid: 0 for cid in customer_ids}
    params = {
        "status":         "any",
        "created_at_max": f"{data_inicio}T00:00:00-03:00",
        "fields":         "id,customer",
        "limit":          250,
    }
    try:
        while True:
            data = _get("orders.json", params)
            pedidos = data.get("orders", [])
            for p in pedidos:
                cid = (p.get("customer") or {}).get("id")
                if cid and cid in contagem:
                    contagem[cid] += 1
            if len(pedidos) < 250:
                break
            params = {**params, "since_id": pedidos[-1]["id"]}
    except Exception as e:
        logger.warning("[pod_shopify] Erro ao buscar histórico recompras: %s", e)
    return contagem
