"""
scripts/sync_pedidos_diario.py

Sync de pedidos pagos Shopify → BigQuery.
Gera pedidos_diario com UTMs para cruzamento com ads_diario.

Cruzamento Meta:   utm_campaign = campaign.id  |  utm_content = ad.id
Cruzamento Google: utm_campaign = campaign name (nome da campanha)

UTMs — por cliente:
  POD: vêm de note_attributes [{name: utm_*, value: ...}]
  SO:  vêm de landing_site (query string: ?utm_source=...&utm_campaign=...)

novo_cliente: True se é o primeiro order_id do cliente no histórico completo do BQ
             (calculado post-load via UPDATE, independente do orders_count atual)

frete: coluna separada de total_price (valor_pago = total_price, frete = shipping_lines soma)

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
from urllib.parse import urlparse, parse_qs

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
        "store_env":    "SHOPIFY_STORE",
        "token_env":    "SHOPIFY_TOKEN",
        "utm_source":   "landing_site",   # UTMs no query string do landing_site
    },
    "pod_kombucha": {
        "store_env":    "SHOPIFY_POD_STORE",
        "token_env":    "SHOPIFY_POD_TOKEN",
        "utm_source":   "note_attributes", # UTMs em note_attributes
    },
}

_FIELDS = (
    "id,created_at,total_price,total_discounts,shipping_lines,"
    "financial_status,customer,source_name,note_attributes,landing_site"
)


def _get_headers(token: str) -> dict:
    return {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}


def _paginar_pedidos(store: str, token: str,
                     date_start: date, date_end: date) -> list[dict]:
    """Busca todos os pedidos pagos no período com cursor pagination."""
    url = f"https://{store.removesuffix('.myshopify.com')}.myshopify.com/admin/api/{API_VERSION}/orders.json"
    headers = _get_headers(token)
    params = {
        "limit":            250,
        "status":           "any",
        "financial_status": "paid",
        "created_at_min":   f"{date_start.isoformat()}T00:00:00-03:00",
        "created_at_max":   f"{date_end.isoformat()}T23:59:59-03:00",
        "fields":           _FIELDS,
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
        link = r.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
                break
        if not next_url:
            break
        url = next_url
        params = {}
    return pedidos


def _utms_from_note_attributes(note_attributes: list) -> dict:
    """Extrai UTMs de note_attributes [{name, value}] — POD."""
    utms = {}
    for attr in (note_attributes or []):
        k = attr.get("name", "").lower()
        v = attr.get("value") or None
        if k in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
            utms[k] = v
    return utms


def _utms_from_landing_site(landing_site: str) -> dict:
    """Extrai UTMs do query string de landing_site — SO."""
    if not landing_site:
        return {}
    try:
        qs = parse_qs(urlparse(landing_site).query)
        return {
            k: qs[k][0] or None
            for k in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
            if k in qs and qs[k]
        }
    except Exception:
        return {}


def _normalizar(pedido: dict, cliente: str, utm_source_type: str, agora_iso: str) -> dict:
    """Converte pedido Shopify para schema pedidos_diario."""
    created_raw = pedido.get("created_at", "")
    try:
        dt_utc  = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        dt_brt  = dt_utc.astimezone(BRT)
        data_brt    = dt_brt.date().isoformat()
        created_utc = dt_utc.isoformat()
    except Exception:
        data_brt    = created_raw[:10]
        created_utc = created_raw

    # UTMs: fonte depende do cliente
    if utm_source_type == "note_attributes":
        utms = _utms_from_note_attributes(pedido.get("note_attributes", []))
    else:
        utms = _utms_from_landing_site(pedido.get("landing_site"))

    # Frete: soma de shipping_lines
    frete = sum(float(s.get("price", 0)) for s in (pedido.get("shipping_lines") or []))

    # novo_cliente: None aqui — será calculado em batch após insert via BQ UPDATE
    customer = pedido.get("customer") or {}

    return {
        "cliente":        cliente,
        "order_id":       str(pedido["id"]),
        "customer_id":    str(customer.get("id", "")) or None,
        "data_brt":       data_brt,
        "created_at_utc": created_utc,
        "status":         pedido.get("financial_status"),
        "valor_pago":     float(pedido.get("total_price") or 0),
        "frete":          round(frete, 2),
        "desconto":       float(pedido.get("total_discounts") or 0),
        "novo_cliente":   None,   # calculado em _atualizar_novo_cliente_bq
        "utm_source":     utms.get("utm_source"),
        "utm_medium":     utms.get("utm_medium"),
        "utm_campaign":   utms.get("utm_campaign"),
        "utm_content":    utms.get("utm_content"),
        "utm_term":       utms.get("utm_term"),
        "source_name":    pedido.get("source_name"),
        "criado_em":      agora_iso,
    }


def _atualizar_novo_cliente_bq(cliente: str) -> int:
    """
    Recalcula novo_cliente no BQ após o MERGE.
    Critério: True se é o PRIMEIRO order_id do customer_id no histórico completo.
    Independente de orders_count atual — não regride com recompras futuras.
    """
    try:
        from db.bq_writer import enabled, _client as bq_client
        if not enabled():
            return 0
        import os as _os
        bq = bq_client()
        project = _os.environ.get("BQ_PROJECT", bq.project)
        dataset = _os.environ.get("BQ_DATASET", "guara_performance")
        p = f"{project}.{dataset}"

        sql = f"""
        UPDATE `{p}.pedidos_diario` T
        SET novo_cliente = (
          T.order_id = (
            SELECT MIN(order_id)
            FROM `{p}.pedidos_diario`
            WHERE cliente = T.cliente
              AND customer_id = T.customer_id
              AND customer_id IS NOT NULL
          )
        )
        WHERE cliente = '{cliente}'
          AND customer_id IS NOT NULL
        """
        job = bq.query(sql)
        job.result()
        return job.num_dml_affected_rows or 0
    except Exception as exc:
        logger.warning("[%s] _atualizar_novo_cliente_bq falha: %s", cliente, exc)
        return 0


def sync_cliente(cliente_key: str, date_start: date, date_end: date) -> int:
    cfg            = _CLIENT_CFG[cliente_key]
    store          = os.environ.get(cfg["store_env"], "").strip()
    token          = os.environ.get(cfg["token_env"], "").strip()
    utm_source_type = cfg["utm_source"]

    if not store or not token:
        logger.error("[%s] store/token não configurados", cliente_key)
        return 0

    logger.info("[%s] Buscando pedidos %s → %s (utm via %s)",
                cliente_key, date_start, date_end, utm_source_type)
    pedidos_raw = _paginar_pedidos(store, token, date_start, date_end)
    logger.info("[%s] %d pedidos recebidos", cliente_key, len(pedidos_raw))

    agora = datetime.now(timezone.utc).isoformat()
    rows  = [_normalizar(p, cliente_key, utm_source_type, agora) for p in pedidos_raw]

    try:
        from db.bq_writer import enabled, _insert
        if enabled():
            _insert("pedidos_diario", rows, f"pedidos/{cliente_key}")
    except Exception as exc:
        logger.warning("[BQ] pedidos/%s falha: %s", cliente_key, exc)

    # Recalcula novo_cliente para todo o histórico do cliente
    n_updated = _atualizar_novo_cliente_bq(cliente_key)
    logger.info("[%s] ✅ %d pedidos | novo_cliente recalculado: %d linhas",
                cliente_key, len(rows), n_updated)
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cliente",     type=str, default=None)
    parser.add_argument("--dias",        type=int, default=8)
    parser.add_argument("--data-inicio", type=str, default=None)
    parser.add_argument("--data-fim",    type=str, default=None)
    args = parser.parse_args()

    ontem      = date.today() - timedelta(days=1)
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
