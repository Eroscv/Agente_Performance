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

Pedido pago (regra do Colab, tutorial "Como calculamos o CAC"): tem pelo
menos uma transação capturada e não cancelada E o status atual não é
cancelled/refused. Toda linha de pedido é gravada (com `pago` e o status
atual), então o gold decide o que conta.

receita_sem_frete = value_products - value_discount.
shopify_order_number = metadata.platform_order_number: na loja pod-kombucha
liga o pedido da Yampi ao pedido Shopify (mesmos pedidos, não somar).

Cliente novo NÃO é calculado aqui: a regra (histórico completo da Shopify,
e-mail na poddi) vive no gold, ver bigquery/scripts/gold/proc_load_gold.sql.

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

# Status atual que nunca conta como pedido pago (Colab, seção 3 do tutorial).
STATUS_NAO_PAGOS = {"cancelled", "refused"}

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
        url = f"{base_url}/orders?page={page}&limit={limit}&include=status,transactions"
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
    meta     = {m.get("key"): m.get("value") for m in (order.get("metadata") or {}).get("data", [])}

    # Pago (regra do Colab): transação capturada e não cancelada + status atual
    # fora de cancelled/refused. Cancelado nunca conta, mesmo com captura.
    transacoes = (order.get("transactions") or {}).get("data", [])
    capturas = [t["captured_at"]["date"] for t in transacoes
                if t.get("captured_at") and not t.get("cancelled")]
    pago = bool(capturas) and status.get("alias") not in STATUS_NAO_PAGOS

    valor_produtos = float(order.get("value_products") or 0)
    valor_desconto = float(order.get("value_discount") or 0)
    email = (customer.get("email") or "").strip().lower() or None

    return {
        "loja":                 loja,
        "order_id":             str(order["id"]),
        "customer_id":          str(customer.get("id")) if customer.get("id") is not None else None,
        "customer_email":       email,
        "shopify_order_number": meta.get("platform_order_number"),
        "data":                 data_brt,
        "created_at_utc":       dt_brt.astimezone(timezone.utc).isoformat() if dt_brt else None,
        "status":               status.get("alias"),
        "pago":                 pago,
        "captured_at":          (datetime.strptime(min(capturas)[:19], "%Y-%m-%d %H:%M:%S")
                                 .replace(tzinfo=BRT).astimezone(timezone.utc).isoformat()
                                 if capturas else None),
        "valor_pago":           float(order.get("value_total") or 0),
        "receita_sem_frete":    round(valor_produtos - valor_desconto, 2),
        "desconto":             valor_desconto,
        "frete":                float(order.get("value_shipment") or 0),
        "utm_source":           order.get("utm_source"),
        "utm_medium":           order.get("utm_medium"),
        "utm_campaign":         order.get("utm_campaign"),
        "utm_content":          order.get("utm_content"),
        "utm_term":             order.get("utm_term"),
        "atualizado_em":        agora_iso,
    }


# Colunas da tabela dooki_pedidos: (nome, tipo SQLite, tipo BigQuery).
# Fonte única para SQLite, schema do BigQuery e MERGE.
COLUNAS = [
    ("loja",                 "TEXT",    "STRING"),
    ("order_id",             "TEXT",    "STRING"),
    ("customer_id",          "TEXT",    "STRING"),
    ("customer_email",       "TEXT",    "STRING"),
    ("shopify_order_number", "TEXT",    "STRING"),
    ("data",                 "TEXT",    "DATE"),
    ("created_at_utc",       "TEXT",    "TIMESTAMP"),
    ("status",               "TEXT",    "STRING"),
    ("pago",                 "INTEGER", "BOOL"),
    ("captured_at",          "TEXT",    "TIMESTAMP"),
    ("valor_pago",           "REAL",    "FLOAT64"),
    ("receita_sem_frete",    "REAL",    "FLOAT64"),
    ("desconto",             "REAL",    "FLOAT64"),
    ("frete",                "REAL",    "FLOAT64"),
    ("utm_source",           "TEXT",    "STRING"),
    ("utm_medium",           "TEXT",    "STRING"),
    ("utm_campaign",         "TEXT",    "STRING"),
    ("utm_content",          "TEXT",    "STRING"),
    ("utm_term",             "TEXT",    "STRING"),
    ("atualizado_em",        "TEXT",    "TIMESTAMP"),
]
CHAVE = ("loja", "order_id")
NOMES = [c[0] for c in COLUNAS]
NAO_CHAVE = [n for n in NOMES if n not in CHAVE]


# ── SQLite ────────────────────────────────────────────────────────────────────

def salvar_sqlite(rows: list[dict]) -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dooki_pedidos ("
        + ", ".join(f"{n} {t}" for n, t, _ in COLUNAS)
        + f", PRIMARY KEY ({', '.join(CHAVE)}))"
    )
    existentes = {r[1] for r in conn.execute("PRAGMA table_info(dooki_pedidos)")}
    for nome, tipo, _ in COLUNAS:
        if nome not in existentes:
            conn.execute(f"ALTER TABLE dooki_pedidos ADD COLUMN {nome} {tipo}")

    sql = (
        f"INSERT INTO dooki_pedidos ({', '.join(NOMES)}) VALUES ({', '.join('?' * len(NOMES))}) "
        f"ON CONFLICT({', '.join(CHAVE)}) DO UPDATE SET "
        + ", ".join(f"{n} = excluded.{n}" for n in NAO_CHAVE)
    )
    for v in rows:
        conn.execute(sql, [int(v[n]) if isinstance(v[n], bool) else v[n] for n in NOMES])
    conn.commit()
    conn.close()
    return len(rows)


# ── BigQuery ──────────────────────────────────────────────────────────────────

BQ_SCHEMA = [bq.SchemaField(n, t) for n, _, t in COLUNAS]


def _ensure_table(client: bq.Client) -> None:
    """Cria a tabela se não existir; se existir, acrescenta colunas novas."""
    table_id = f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
    try:
        tabela = client.get_table(table_id)
    except Exception:
        table = bq.Table(table_id, schema=BQ_SCHEMA)
        table.time_partitioning = bq.TimePartitioning(field="data")
        client.create_table(table)
        logger.info("[BQ] Tabela %s criada", table_id)
        return

    existentes = {f.name for f in tabela.schema}
    faltando = [f for f in BQ_SCHEMA if f.name not in existentes]
    if faltando:
        client.query(
            f"ALTER TABLE `{table_id}` "
            + ", ".join(f"ADD COLUMN IF NOT EXISTS {f.name} {f.field_type}" for f in faltando)
        ).result()
        logger.info("[BQ] Colunas adicionadas em %s: %s", BQ_TABLE, [f.name for f in faltando])


def salvar_bigquery(client: bq.Client, rows: list[dict], loja: str) -> int:
    """Upsert por (loja, order_id) via MERGE — nunca perde pedido quando o status muda."""
    if not rows:
        return 0

    _ensure_table(client)

    try:
        staging_id = f"{BQ_PROJECT}.{BQ_DATASET}._staging_dooki_pedidos_{loja}"
        job_config = bq.LoadJobConfig(write_disposition="WRITE_TRUNCATE", schema=BQ_SCHEMA)
        client.load_table_from_json(rows, staging_id, job_config=job_config).result()

        set_clause = ", ".join(f"{c} = S.{c}" for c in NAO_CHAVE)
        merge_sql = f"""
        MERGE `{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}` T
        USING `{staging_id}` S
        ON T.loja = S.loja AND T.order_id = S.order_id
        WHEN MATCHED THEN
          UPDATE SET {set_clause}
        WHEN NOT MATCHED THEN
          INSERT ({', '.join(NOMES)}) VALUES ({', '.join('S.' + c for c in NOMES)})
        """
        client.query(merge_sql).result()
        client.delete_table(staging_id, not_found_ok=True)

        logger.info("[BQ] ✅ %d pedidos upsert em %s (loja=%s)", len(rows), BQ_TABLE, loja)
        return len(rows)
    except Exception as e:
        logger.error("[BQ] Exceção (loja=%s): %s", loja, e)
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
        logger.info("[%s] BigQuery: %d pedidos upsert", nome, n_bq)


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
