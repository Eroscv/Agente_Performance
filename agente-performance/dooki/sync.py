"""
dooki/sync.py

Sincroniza pedidos (por pedido, não mais por total diário) das lojas
Yampi/Dooki da POD Kombucha para:
  - SQLite local (tabela: dooki_pedidos, por loja)
  - BigQuery (guara-db.guara_performance.dooki_pedidos — uma linha por
    pedido, coluna `loja` identifica poddi/pod_kombucha/pod8)

A POD Kombucha vende em 3 lojas Yampi/Dooki separadas (poddi, pod_kombucha,
pod8). Cada pedido é upsertado por (loja, order_id) — se o status mudar
depois (paid → on_carriage → delivered, ou → cancelled), a próxima
sincronização só atualiza aquele pedido no lugar, nunca perde o histórico
do dia (era exatamente isso que acontecia quando a gente só gravava o
total agregado por dia).

Cruzamento com ads (mesmo padrão do Shopify, ver scripts/sync_pedidos_diario.py):
  Meta:   utm_campaign = campaign.id  |  utm_content = ad.id

Receita confirmada: status.alias em {paid, on_carriage, delivered}.
'cancelled' e outros ficam de fora (mas a linha do pedido continua
gravada, com o status atual, pra auditoria).

novo_cliente: True se é o primeiro order_id do customer_id no histórico
             completo daquela loja (calculado post-load via UPDATE no BQ,
             mesmo critério usado pro Shopify).

Uso:
  python3 -m dooki.sync --dias 30
  python3 -m dooki.sync --dias 30 --dry-run
  python3 -m dooki.sync --dias 30 --loja pod8
"""

import argparse
import logging
import os
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from google.cloud import bigquery as bq
from google.oauth2 import service_account as _sa

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent.parent
ENV_FILE   = BASE_DIR / ".env"
DB_PATH    = BASE_DIR / "data" / "performance" / "pod_kombucha" / "dooki.db"
BQ_CREDS   = BASE_DIR / "credentials" / "bq-service-account.json"

load_dotenv(ENV_FILE)

BQ_PROJECT = "guara-db"
BQ_DATASET = "guara_performance"
BQ_TABLE   = "dooki_pedidos"   # tabela por pedido (nova) — substitui dooki_pedidos_diario no gold

BRT = timezone(timedelta(hours=-3))

# Lojas Yampi/Dooki da POD Kombucha. Cada uma tem credenciais próprias.
LOJAS = [
    {
        "nome":         "poddi",
        "base_url_env": "DOOKI_PODDI_BASE_URL",
        "token_env":    "DOOKI_PODDI_USER_TOKEN",
        "secret_env":   "DOOKI_PODDI_USER_SECRET_KEY",
    },
    {
        "nome":         "pod_kombucha",
        "base_url_env": "DOOKI_POD_KOMBUCHA_BASE_URL",
        "token_env":    "DOOKI_POD_KOMBUCHA_USER_TOKEN",
        "secret_env":   "DOOKI_POD_KOMBUCHA_USER_SECRET_KEY",
    },
    {
        "nome":         "pod8",
        "base_url_env": "DOOKI_POD8_BASE_URL",
        # pod8 usa o mesmo token/secret da loja poddi (mesma conta Dooki/Yampi)
        "token_env":    "DOOKI_PODDI_USER_TOKEN",
        "secret_env":   "DOOKI_PODDI_USER_SECRET_KEY",
    },
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── API Dooki ─────────────────────────────────────────────────────────────────

def _headers(token: str, secret: str) -> dict:
    return {
        "User-Token":       token,
        "User-Secret-Key":  secret,
        "Content-Type":     "application/json",
    }


def buscar_pedidos(base_url: str, token: str, secret: str,
                    data_inicio: date, data_fim: date) -> list[dict]:
    """
    Retorna TODOS os pedidos (qualquer status) com created_at no intervalo
    [data_inicio, data_fim] — a filtragem por status acontece depois, na
    normalização, pra manter o histórico do pedido mesmo quando ele não
    conta como receita confirmada (ex: cancelled).

    A API Dooki retorna pedidos em ordem DESC. Paginamos da pág 1 em diante
    e paramos quando a data sair do período.
    """
    pedidos = []
    limit   = 100
    data_inicio_str = data_inicio.isoformat()
    data_fim_str    = data_fim.isoformat()
    headers = _headers(token, secret)

    for page in range(1, 100):  # max 99 páginas
        url = f"{base_url}/orders?page={page}&limit={limit}"
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 400:
            break
        r.raise_for_status()
        orders = r.json().get("data", [])
        if not orders:
            break

        passou_inicio = False
        for order in orders:  # DESC: mais recente primeiro
            data_criacao = order["created_at"]["date"][:10]
            if data_criacao > data_fim_str:
                continue   # ainda mais recente que o período
            if data_criacao < data_inicio_str:
                passou_inicio = True
                break       # já passou do início, pode parar
            pedidos.append(order)

        last_date = orders[-1]["created_at"]["date"][:10] if orders else ""
        if passou_inicio or (last_date and last_date < data_inicio_str):
            break
        time.sleep(0.1)

    return pedidos


def _normalizar(order: dict, loja: str, agora_iso: str) -> dict:
    """Converte um pedido Dooki/Yampi pro schema dooki_pedidos."""
    created_raw = order["created_at"]["date"]
    dt_brt = None
    try:
        # A API já devolve no fuso America/Sao_Paulo (ver created_at.timezone)
        dt_brt = datetime.strptime(created_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BRT)
        data_brt = dt_brt.date().isoformat()
    except Exception:
        data_brt = created_raw[:10]

    customer = (order.get("customer") or {}).get("data") or {}
    status   = (order.get("status") or {}).get("data") or {}

    return {
        "loja":           loja,
        "order_id":       str(order["id"]),
        "customer_id":    str(customer.get("id")) if customer.get("id") is not None else None,
        "data":           data_brt,
        "created_at_utc": dt_brt.astimezone(timezone.utc).isoformat() if dt_brt else None,
        "status":         status.get("alias"),
        "valor_pago":     float(order.get("value_total") or 0),
        "desconto":       float(order.get("value_discount") or 0),
        "frete":          float(order.get("value_shipment") or 0),
        "novo_cliente":   None,  # calculado em batch após upsert
        "utm_source":     order.get("utm_source"),
        "utm_medium":     order.get("utm_medium"),
        "utm_campaign":   order.get("utm_campaign"),
        "utm_content":    order.get("utm_content"),
        "utm_term":       order.get("utm_term"),
        "atualizado_em":  agora_iso,
    }


# ── SQLite ────────────────────────────────────────────────────────────────────

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS dooki_pedidos (
    loja            TEXT NOT NULL,
    order_id        TEXT NOT NULL,
    customer_id     TEXT,
    data            TEXT NOT NULL,
    created_at_utc  TEXT,
    status          TEXT,
    valor_pago      REAL DEFAULT 0,
    desconto        REAL DEFAULT 0,
    frete           REAL DEFAULT 0,
    novo_cliente    INTEGER,
    utm_source      TEXT,
    utm_medium      TEXT,
    utm_campaign    TEXT,
    utm_content     TEXT,
    utm_term        TEXT,
    atualizado_em   TEXT NOT NULL,
    PRIMARY KEY (loja, order_id)
)
"""


def salvar_sqlite(rows: list[dict]) -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(CREATE_TABLE)
    n = 0
    for v in rows:
        conn.execute("""
            INSERT INTO dooki_pedidos
              (loja, order_id, customer_id, data, created_at_utc, status,
               valor_pago, desconto, frete, novo_cliente,
               utm_source, utm_medium, utm_campaign, utm_content, utm_term, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(loja, order_id) DO UPDATE SET
              customer_id    = excluded.customer_id,
              data           = excluded.data,
              created_at_utc = excluded.created_at_utc,
              status         = excluded.status,
              valor_pago     = excluded.valor_pago,
              desconto       = excluded.desconto,
              frete          = excluded.frete,
              utm_source     = excluded.utm_source,
              utm_medium     = excluded.utm_medium,
              utm_campaign   = excluded.utm_campaign,
              utm_content    = excluded.utm_content,
              utm_term       = excluded.utm_term,
              atualizado_em  = excluded.atualizado_em
        """, (v["loja"], v["order_id"], v["customer_id"], v["data"], v["created_at_utc"],
              v["status"], v["valor_pago"], v["desconto"], v["frete"],
              v["utm_source"], v["utm_medium"], v["utm_campaign"], v["utm_content"],
              v["utm_term"], v["atualizado_em"]))
        n += 1
    conn.commit()
    conn.close()
    return n


# ── BigQuery ──────────────────────────────────────────────────────────────────

BQ_SCHEMA = [
    bq.SchemaField("loja", "STRING"),
    bq.SchemaField("order_id", "STRING"),
    bq.SchemaField("customer_id", "STRING"),
    bq.SchemaField("data", "DATE"),
    bq.SchemaField("created_at_utc", "TIMESTAMP"),
    bq.SchemaField("status", "STRING"),
    bq.SchemaField("valor_pago", "FLOAT64"),
    bq.SchemaField("desconto", "FLOAT64"),
    bq.SchemaField("frete", "FLOAT64"),
    bq.SchemaField("utm_source", "STRING"),
    bq.SchemaField("utm_medium", "STRING"),
    bq.SchemaField("utm_campaign", "STRING"),
    bq.SchemaField("utm_content", "STRING"),
    bq.SchemaField("utm_term", "STRING"),
    bq.SchemaField("atualizado_em", "TIMESTAMP"),
]


def _ensure_table(client: bq.Client) -> None:
    table_id = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    try:
        client.get_table(table_id)
    except Exception:
        table = bq.Table(table_id, schema=BQ_SCHEMA + [bq.SchemaField("novo_cliente", "BOOL")])
        table.time_partitioning = bq.TimePartitioning(field="data")
        client.create_table(table)
        logger.info("[BQ] Tabela %s criada", table_id)


def salvar_bigquery(client: bq.Client, rows: list[dict], loja: str) -> int:
    """Upsert por (loja, order_id) via MERGE — nunca perde pedido quando o status muda."""
    if not rows:
        return 0

    _ensure_table(client)

    for r in rows:
        r.pop("novo_cliente", None)

    try:
        staging_id = f"{BQ_PROJECT}.{BQ_DATASET}._staging_dooki_pedidos_{loja}"
        job_config = bq.LoadJobConfig(write_disposition="WRITE_TRUNCATE", schema=BQ_SCHEMA)
        client.load_table_from_json(rows, staging_id, job_config=job_config).result()

        cols = [f.name for f in BQ_SCHEMA]
        set_clause = ", ".join(f"{c} = S.{c}" for c in cols if c not in ("loja", "order_id"))
        insert_cols = ", ".join(cols)
        insert_vals = ", ".join(f"S.{c}" for c in cols)

        merge_sql = f"""
        MERGE `{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}` T
        USING `{staging_id}` S
        ON T.loja = S.loja AND T.order_id = S.order_id
        WHEN MATCHED THEN
          UPDATE SET {set_clause}
        WHEN NOT MATCHED THEN
          INSERT ({insert_cols}) VALUES ({insert_vals})
        """
        client.query(merge_sql).result()
        client.delete_table(staging_id, not_found_ok=True)

        logger.info("[BQ] ✅ %d pedidos upsert em %s (loja=%s)", len(rows), BQ_TABLE, loja)
        return len(rows)
    except Exception as e:
        logger.error("[BQ] Exceção (loja=%s): %s", loja, e)
        return 0


def atualizar_novo_cliente_bq(client: bq.Client, loja: str) -> int:
    """
    Recalcula novo_cliente no BQ: True se o order_id é o PRIMEIRO (menor)
    daquele customer_id, dentro da mesma loja (customer_id não é comparável
    entre lojas — são contas Yampi separadas).
    """
    try:
        sql = f"""
        UPDATE `{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}` T
        SET novo_cliente = (
          T.order_id = (
            SELECT MIN(order_id)
            FROM `{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}`
            WHERE loja = T.loja
              AND customer_id = T.customer_id
              AND customer_id IS NOT NULL
          )
        )
        WHERE loja = '{loja}'
          AND customer_id IS NOT NULL
        """
        job = client.query(sql)
        job.result()
        return job.num_dml_affected_rows or 0
    except Exception as exc:
        logger.warning("[%s] atualizar_novo_cliente_bq falha: %s", loja, exc)
        return 0


# ── Main ──────────────────────────────────────────────────────────────────────

def sync_loja(client: bq.Client | None, loja: dict, data_ini: date, data_fim: date,
              dry_run: bool) -> None:
    nome   = loja["nome"]
    base_url = os.getenv(loja["base_url_env"], "").rstrip("/")
    token    = os.getenv(loja["token_env"], "")
    secret   = os.getenv(loja["secret_env"], "")

    if not base_url or not token or not secret:
        logger.warning("Loja '%s' sem credenciais configuradas — pulando", nome)
        return

    logger.info("[%s] Buscando pedidos: %s → %s", nome, data_ini, data_fim)
    orders = buscar_pedidos(base_url, token, secret, data_ini, data_fim)
    logger.info("[%s] %d pedidos encontrados", nome, len(orders))

    if not orders:
        return

    agora = datetime.now(timezone.utc).isoformat()
    rows  = [_normalizar(o, nome, agora) for o in orders]

    if dry_run:
        print(f"\n=== DRY RUN ({nome}) — {len(rows)} pedidos ===")
        for v in rows[:10]:
            print(f"  {v['order_id']} | {v['data']} | {v['status']} | R${v['valor_pago']:.2f} | "
                  f"utm_content={v['utm_content']}")
        if len(rows) > 10:
            print(f"  ... e mais {len(rows) - 10}")
        return

    n_sqlite = salvar_sqlite(rows)
    logger.info("[%s] SQLite: %d pedidos salvos em %s", nome, n_sqlite, DB_PATH)

    if client is not None:
        n_bq = salvar_bigquery(client, rows, nome)
        n_nc = atualizar_novo_cliente_bq(client, nome)
        logger.info("[%s] BigQuery: %d pedidos upsert | novo_cliente recalculado: %d linhas",
                    nome, n_bq, n_nc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias",    type=int, default=30, help="Dias para sincronizar (padrão: 30)")
    parser.add_argument("--dry-run", action="store_true",  help="Não salva, só exibe")
    parser.add_argument("--loja",    choices=[l["nome"] for l in LOJAS], default=None,
                         help="Sincroniza só uma loja (padrão: todas)")
    args = parser.parse_args()

    hoje      = date.today()
    data_fim  = hoje - timedelta(days=1)   # ontem (dia corrente incompleto)
    data_ini  = hoje - timedelta(days=args.dias)

    lojas = [l for l in LOJAS if args.loja is None or l["nome"] == args.loja]

    client = None
    if not args.dry_run:
        try:
            creds = _sa.Credentials.from_service_account_file(
                str(BQ_CREDS), scopes=["https://www.googleapis.com/auth/bigquery"]
            )
            client = bq.Client(project=BQ_PROJECT, credentials=creds)
        except Exception as e:
            logger.error("[BQ] Não foi possível criar client: %s", e)

    for loja in lojas:
        sync_loja(client, loja, data_ini, data_fim, args.dry_run)


if __name__ == "__main__":
    main()
