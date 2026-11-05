"""
scripts/sync_shopify_clientes_historico.py

Primeiro pedido por cliente da Shopify da POD Kombucha, usado pelo gold para a
regra de "cliente novo" do Colab (tutorial "Como calculamos o CAC — Pod
Kombucha", seção 4):

  - pod.com.br: cliente novo = o primeiro pedido da vida dele na Shopify é
    este pedido (tolerância de 5 minutos).
  - poddi.com.br: cliente novo exige que o e-mail nunca tenha comprado no
    pod.com.br antes do pedido.

Limite da Shopify: sem o escopo `read_all_orders`, a API de pedidos só enxerga
os últimos 60 dias. Por isso o primeiro pedido é inferido assim, por cliente:
  - orders_count (customers.json) <= pedidos do cliente na janela de 60 dias:
    todos os pedidos dele estão visíveis, o primeiro é o mais antigo da janela;
  - caso contrário (ou cliente sem pedido na janela): ele já tinha pedido
    anterior à janela, então o primeiro pedido recebe um carimbo antigo
    (1970-01-01), que nunca cai a 5 min de nenhum pedido recente.
Reproduz 90/90 as flags de cliente novo do CSV da Bia em 01/11.

Grava guara_performance.shopify_clientes_historico (uma linha por
(cliente, customer_id, e-mail)). O MERGE só antecipa o primeiro pedido
real: um primeiro pedido já gravado nunca é trocado pelo carimbo antigo quando
a janela deslizante deixa de enxergá-lo, nem "volta" para recorrente quando o
cliente compra de novo. Só a carga --full (visibilidade completa da janela de
60 dias) pode trocar um carimbo antigo por um primeiro pedido real (reparo).

Uso:
    python3 scripts/sync_shopify_clientes_historico.py --full     # todos os clientes (1ª carga)
    python3 scripts/sync_shopify_clientes_historico.py --dias 45  # incremental (cron)
"""
import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from google.cloud import bigquery as bq
from google.oauth2 import service_account

from scripts.sync_pedidos_diario import _paginar_pedidos, _get_headers, API_VERSION

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

CLIENTE      = "pod_kombucha"
BQ_PROJECT   = "guara-db"
BQ_DATASET   = "guara_performance"
BQ_TABLE     = "shopify_clientes_historico"
BQ_CREDS     = BASE_DIR / "credentials" / "bq-service-account.json"
JANELA_MAX   = 60   # limite da API de pedidos sem read_all_orders
ANTES_JANELA = datetime(1970, 1, 1, tzinfo=timezone.utc)

SCHEMA = [
    bq.SchemaField("cliente", "STRING"),
    bq.SchemaField("customer_id", "STRING"),
    bq.SchemaField("email", "STRING"),
    bq.SchemaField("primeiro_pedido_em", "TIMESTAMP"),
    bq.SchemaField("primeiro_order_id", "STRING"),
    bq.SchemaField("atualizado_em", "TIMESTAMP"),
]


def _paginar_clientes(store: str, token: str, ids: list[str] | None = None) -> list[dict]:
    """Clientes (id, email, orders_count). Sem `ids`, pagina a base inteira."""
    headers = _get_headers(token)
    base = f"https://{store}.myshopify.com/admin/api/{API_VERSION}/customers.json"
    fields = "id,email,orders_count"

    if ids is not None:
        out = []
        for i in range(0, len(ids), 100):
            r = requests.get(base, headers=headers, timeout=30,
                             params={"ids": ",".join(ids[i:i + 100]), "limit": 250, "fields": fields})
            r.raise_for_status()
            out.extend(r.json().get("customers", []))
        return out

    out, url, params = [], base, {"limit": 250, "fields": fields}
    while url:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2)))
            continue
        r.raise_for_status()
        out.extend(r.json().get("customers", []))
        url, params = None, {}
        for part in r.headers.get("Link", "").split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
    return out


def _construir(pedidos: list[dict], clientes: list[dict]) -> list[dict]:
    """Primeiro pedido por cliente, inferido como descrito no cabeçalho."""
    agora = datetime.now(timezone.utc).isoformat()
    na_janela = defaultdict(list)
    for p in pedidos:
        cid = (p.get("customer") or {}).get("id")
        if cid:
            na_janela[str(cid)].append((datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")), str(p["id"])))

    rows = []
    for c in clientes:
        cid, total = str(c["id"]), c.get("orders_count") or 0
        if total < 1:
            continue
        visiveis = sorted(na_janela.get(cid, []))
        if visiveis and total <= len(visiveis):
            primeiro, order_id = visiveis[0]
        else:
            primeiro, order_id = ANTES_JANELA, None
        rows.append({
            "cliente":            CLIENTE,
            "customer_id":        cid,
            "email":              (c.get("email") or "").strip().lower() or None,
            "primeiro_pedido_em": primeiro.astimezone(timezone.utc).isoformat(),
            "primeiro_order_id":  order_id,
            "atualizado_em":      agora,
        })
    return rows


def _gravar(client: bq.Client, rows: list[dict], reparar: bool) -> int:
    table_id = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    try:
        client.get_table(table_id)
    except Exception:
        client.create_table(bq.Table(table_id, schema=SCHEMA))
        logger.info("Tabela %s criada", table_id)

    staging = f"{BQ_PROJECT}.{BQ_DATASET}._staging_{BQ_TABLE}"
    client.load_table_from_json(
        rows, staging, job_config=bq.LoadJobConfig(write_disposition="WRITE_TRUNCATE", schema=SCHEMA)
    ).result()
    client.query(f"""
    MERGE `{table_id}` T
    USING `{staging}` S
    ON T.cliente = S.cliente AND T.customer_id = S.customer_id
       AND IFNULL(T.email, '') = IFNULL(S.email, '')
    WHEN MATCHED AND S.primeiro_pedido_em > TIMESTAMP '1970-01-01'
         AND (S.primeiro_pedido_em < T.primeiro_pedido_em
              OR ({"TRUE" if reparar else "FALSE"} AND T.primeiro_pedido_em = TIMESTAMP '1970-01-01')) THEN
      UPDATE SET primeiro_pedido_em = S.primeiro_pedido_em,
                 primeiro_order_id  = S.primeiro_order_id,
                 atualizado_em      = S.atualizado_em
    WHEN NOT MATCHED THEN
      INSERT (cliente, customer_id, email, primeiro_pedido_em, primeiro_order_id, atualizado_em)
      VALUES (S.cliente, S.customer_id, S.email, S.primeiro_pedido_em, S.primeiro_order_id, S.atualizado_em)
    """).result()
    client.delete_table(staging, not_found_ok=True)
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="Todos os clientes da loja (1ª carga)")
    parser.add_argument("--dias", type=int, default=45, help="Janela de pedidos (máx. 60; padrão: 45)")
    args = parser.parse_args()

    store = os.environ.get("SHOPIFY_POD_STORE", "").strip()
    token = os.environ.get("SHOPIFY_POD_TOKEN", "").strip()
    if not store or not token:
        logger.error("SHOPIFY_POD_STORE/SHOPIFY_POD_TOKEN não configurados")
        sys.exit(1)

    dias = JANELA_MAX if args.full else min(args.dias, JANELA_MAX)
    fim = date.today()
    logger.info("Buscando pedidos (qualquer status) %s → %s", fim - timedelta(days=dias), fim)
    pedidos = _paginar_pedidos(store, token, fim - timedelta(days=dias), fim,
                               financial_status=None, fields="id,created_at,customer")
    logger.info("%d pedidos recebidos", len(pedidos))

    if args.full:
        clientes = _paginar_clientes(store, token)
    else:
        ids = sorted({str(p["customer"]["id"]) for p in pedidos if (p.get("customer") or {}).get("id")})
        clientes = _paginar_clientes(store, token, ids)
    logger.info("%d clientes recebidos", len(clientes))

    rows = _construir(pedidos, clientes)
    creds = service_account.Credentials.from_service_account_file(
        str(BQ_CREDS), scopes=["https://www.googleapis.com/auth/bigquery"])
    n = _gravar(bq.Client(project=BQ_PROJECT, credentials=creds), rows, reparar=args.full)
    logger.info("✅ %d clientes gravados em %s", n, BQ_TABLE)


if __name__ == "__main__":
    main()
