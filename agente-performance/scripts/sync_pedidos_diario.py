"""
scripts/sync_pedidos_diario.py

Sync de pedidos pagos Shopify → SQLite + BigQuery.
Gera uma tabela pedidos_diario com UTMs para cruzamento com ads_diario.

Cruzamento Meta:   pedidos_diario.utm_campaign = ads_diario.campaign_id
                   pedidos_diario.utm_content  = ads_diario.ad_id
Cruzamento Google: pedidos_diario.utm_campaign = ads_diario.campaign_id (nome)

UTMs vêm de note_attributes (campo customizado do checkout).
novo_cliente: True se customer.orders_count == 1 no momento do pedido.

Uso:
    python scripts/sync_pedidos_diario.py
    python scripts/sync_pedidos_diario.py --cliente pod_kombucha --dias 30
    python scripts/sync_pedidos_diario.py --data-inicio 2026-10-01 --data-fim 2026-10-18
"""
import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

import os
import requests

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))
API_VERSION = "2025-01"

_CLIENT_CFG = {
    "source_of": {
        "store_env": "SHOPIFY_STORE",
        "token_env": "SHOPIFY_TOKEN",
    },
    "pod_kombucha": {
        "store_env": "SHOPIFY_POD_STORE",
        "token_env": "SHOPIFY_POD_TOKEN",
    },
}


def _get_headers(token: str) -> dict:
    return {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}


def _paginar_pedidos(store: str, token: str,
                     date_start: date, date_end: date) -> list[dict]:
    """Busca todos os pedidos pagos no período via REST API com cursor pagination."""
    url = f"https://{store}.myshopify.com/admin/api/{API_VERSION}/orders.json"
    headers = _get_headers(token)
    fields = ("id,created_at,total_price,total_discounts,"
              "financial_status,customer,source_name,note_attributes")
    params = {
        "limit":            250,
        "status":           "any",
        "financial_status": "paid",
        "created_at_min":   f"{date_start.isoformat()}T00:00:00-03:00",
        "created_at_max":   f"{date_end.isoformat()}T23:59:59-03:00",
        "fields":           fields,
    }
    pedidos = []
    while True:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code == 429:
            import time; time.sleep(float(r.headers.get("Retry-After", 2)))
            continue
        r.raise_for_status()
        batch = r.json().get("orders", [])
        pedidos.extend(batch)
        if not batch or len(batch) < 250:
            break
        # Cursor pagination via Link header
        link = r.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
                break
        if not next_url:
            break
        url = next_url
        params = {}  # URL já tem todos os params
    return pedidos


def _extrair_utms(note_attributes: list) -> dict:
    """Extrai UTMs de note_attributes [{name, value}]."""
    utms = {}
    for attr in (note_attributes or []):
        k = attr.get("name", "").lower()
        v = attr.get("value", "")
        if k in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
            utms[k] = v or None
    return utms


def _normalizar(pedido: dict, cliente: str, agora_iso: str) -> dict:
    """Converte um pedido Shopify para o schema de pedidos_diario."""
    created_raw = pedido.get("created_at", "")
    # Converte para BRT
    try:
        dt_utc = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        dt_brt = dt_utc.astimezone(BRT)
        data_brt    = dt_brt.date().isoformat()
        created_utc = dt_utc.isoformat()
    except Exception:
        data_brt    = created_raw[:10]
        created_utc = created_raw

    utms = _extrair_utms(pedido.get("note_attributes", []))

    customer = pedido.get("customer") or {}
    orders_count = int(customer.get("orders_count") or 1)
    novo_cliente = orders_count == 1

    return {
        "cliente":        cliente,
        "order_id":       str(pedido["id"]),
        "data_brt":       data_brt,
        "created_at_utc": created_utc,
        "status":         pedido.get("financial_status"),
        "valor_pago":     float(pedido.get("total_price") or 0),
        "desconto":       float(pedido.get("total_discounts") or 0),
        "novo_cliente":   novo_cliente,
        "utm_source":     utms.get("utm_source"),
        "utm_medium":     utms.get("utm_medium"),
        "utm_campaign":   utms.get("utm_campaign"),
        "utm_content":    utms.get("utm_content"),
        "utm_term":       utms.get("utm_term"),
        "source_name":    pedido.get("source_name"),
        "criado_em":      agora_iso,
    }


def _write_bq(rows: list[dict], cliente: str) -> None:
    """MERGE na pedidos_diario do BQ. Chave: (cliente, order_id)."""
    if not rows:
        return
    try:
        from db.bq_writer import enabled, _client, _insert
        if not enabled():
            return
        _insert("pedidos_diario", rows, f"pedidos/{cliente}")
    except Exception as exc:
        logger.warning("[BQ] pedidos/%s falha silenciosa: %s", cliente, exc)


def sync_cliente(cliente_key: str, date_start: date, date_end: date) -> int:
    cfg   = _CLIENT_CFG[cliente_key]
    store = os.environ.get(cfg["store_env"], "").strip()
    token = os.environ.get(cfg["token_env"], "").strip()
    if not store or not token:
        logger.error("[%s] store/token não configurados", cliente_key)
        return 0

    logger.info("[%s] Buscando pedidos %s → %s", cliente_key, date_start, date_end)
    pedidos_raw = _paginar_pedidos(store, token, date_start, date_end)
    logger.info("[%s] %d pedidos recebidos", cliente_key, len(pedidos_raw))

    agora = datetime.now(timezone.utc).isoformat()
    rows = [_normalizar(p, cliente_key, agora) for p in pedidos_raw]

    _write_bq(rows, cliente_key)
    logger.info("[%s] ✅ %d pedidos → BQ", cliente_key, len(rows))
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cliente",      type=str, default=None)
    parser.add_argument("--dias",         type=int, default=8)
    parser.add_argument("--data-inicio",  type=str, default=None)
    parser.add_argument("--data-fim",     type=str, default=None)
    args = parser.parse_args()

    ontem = date.today() - timedelta(days=1)
    date_end   = date.fromisoformat(args.data_fim)   if args.data_fim   else ontem
    date_start = date.fromisoformat(args.data_inicio) if args.data_inicio else \
                 date_end - timedelta(days=args.dias - 1)

    clientes = [args.cliente] if args.cliente else list(_CLIENT_CFG)
    total = 0
    for key in clientes:
        total += sync_cliente(key, date_start, date_end)

    logger.info("Sync pedidos concluído: %d pedidos processados", total)


if __name__ == "__main__":
    main()
