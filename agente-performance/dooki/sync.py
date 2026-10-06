"""
dooki/sync.py

Sincroniza pedidos PAGOS da Dooki (POD Kombucha) para:
  - SQLite local (tabela: dooki_pedidos_diario)
  - BigQuery (guara-db.guara_performance.shopify_diario — coluna fonte='dooki')

Regra de receita paga:
  Apenas pedidos com status.alias = 'paid' são contabilizados como receita.
  Pedidos 'invoiced', 'on_carriage', 'cancelled' etc. são ignorados no totalizador.

Uso:
  python3 -m dooki.sync --dias 30
  python3 -m dooki.sync --dias 30 --dry-run
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

DOOKI_BASE_URL   = os.getenv("DOOKI_BASE_URL", "").rstrip("/")
DOOKI_TOKEN      = os.getenv("DOOKI_USER_TOKEN", "")
DOOKI_SECRET_KEY = os.getenv("DOOKI_USER_SECRET_KEY", "")

BQ_PROJECT = "guara-db"
BQ_DATASET = "guara_performance"
BQ_TABLE   = "dooki_pedidos_diario"   # tabela dedicada Dooki no BQ

# Apenas estes status contam como receita paga
STATUS_PAGOS = {"paid", "invoiced", "on_carriage", "delivered"}
# "invoiced" = nota fiscal emitida (pagamento confirmado), "on_carriage" = a caminho
# Receita "conservadora": só paid
STATUS_RECEITA_PAGA = {"paid"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── API Dooki ─────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "User-Token":       DOOKI_TOKEN,
        "User-Secret-Key":  DOOKI_SECRET_KEY,
        "Content-Type":     "application/json",
    }


def buscar_pedidos_pagos(data_inicio: date, data_fim: date) -> list[dict]:
    """
    Retorna pedidos com status 'paid' no intervalo [data_inicio, data_fim].

    A API Dooki retorna pedidos em ordem DESC (mais recente primeiro, pág 1).
    Vai até pág 99 no máximo (400 acima disso).
    Paginamos da pág 1 em diante e paramos quando a data sair do período.
    """
    pedidos = []
    limit   = 100
    data_inicio_str = data_inicio.isoformat()
    data_fim_str    = data_fim.isoformat()

    for page in range(1, 100):  # max 99 páginas
        url = f"{DOOKI_BASE_URL}/orders?page={page}&limit={limit}"
        r = requests.get(url, headers=_headers(), timeout=30)
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
            status_alias = order.get("status", {}).get("data", {}).get("alias", "")
            if status_alias in STATUS_RECEITA_PAGA:
                pedidos.append(order)

        last_date = orders[-1]["created_at"]["date"][:10] if orders else ""
        logger.debug("Pág %d: última_data=%s | coletados=%d", page, last_date, len(pedidos))

        if passou_inicio or (last_date and last_date < data_inicio_str):
            break
        time.sleep(0.1)

    return pedidos


def agregar_por_dia(pedidos: list[dict]) -> dict[str, dict]:
    """
    Agrega pedidos pagos por data de criação.
    Retorna dict[data_str] = {receita, pedidos, ticket_medio}
    """
    por_dia: dict[str, dict] = {}
    for o in pedidos:
        data_str = o["created_at"]["date"][:10]
        valor = float(o.get("value_total") or 0)
        customer_id = str(o.get("customer_id") or "")

        if data_str not in por_dia:
            por_dia[data_str] = {"receita": 0.0, "pedidos": 0, "customer_ids": set()}

        por_dia[data_str]["receita"]    += valor
        por_dia[data_str]["pedidos"]    += 1
        por_dia[data_str]["customer_ids"].add(customer_id)

    # Calcula ticket médio e transforma set em count
    for d, v in por_dia.items():
        v["ticket_medio"]    = round(v["receita"] / v["pedidos"], 2) if v["pedidos"] else 0
        v["clientes_unicos"] = len(v["customer_ids"])
        del v["customer_ids"]

    return por_dia


# ── SQLite ────────────────────────────────────────────────────────────────────

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS dooki_pedidos_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data            TEXT NOT NULL,
    receita         REAL DEFAULT 0,
    pedidos         INTEGER DEFAULT 0,
    ticket_medio    REAL DEFAULT 0,
    clientes_unicos INTEGER DEFAULT 0,
    fonte           TEXT DEFAULT 'dooki',
    atualizado_em   TEXT NOT NULL,
    UNIQUE(data)
)
"""


def salvar_sqlite(por_dia: dict[str, dict]) -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(CREATE_TABLE)
    agora = datetime.now(timezone.utc).isoformat()
    n = 0
    for data_str, v in por_dia.items():
        conn.execute("""
            INSERT INTO dooki_pedidos_diario
              (data, receita, pedidos, ticket_medio, clientes_unicos, fonte, atualizado_em)
            VALUES (?, ?, ?, ?, ?, 'dooki', ?)
            ON CONFLICT(data) DO UPDATE SET
              receita         = excluded.receita,
              pedidos         = excluded.pedidos,
              ticket_medio    = excluded.ticket_medio,
              clientes_unicos = excluded.clientes_unicos,
              atualizado_em   = excluded.atualizado_em
        """, (data_str, round(v["receita"], 2), v["pedidos"],
              v["ticket_medio"], v["clientes_unicos"], agora))
        n += 1
    conn.commit()
    conn.close()
    return n


# ── BigQuery ──────────────────────────────────────────────────────────────────

def salvar_bigquery(por_dia: dict[str, dict]) -> int:
    try:
        creds = _sa.Credentials.from_service_account_file(
            str(BQ_CREDS), scopes=["https://www.googleapis.com/auth/bigquery"]
        )
        client = bq.Client(project=BQ_PROJECT, credentials=creds)
        table  = client.get_table(f"{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}")

        agora = datetime.now(timezone.utc).isoformat()
        rows = []
        for data_str, v in por_dia.items():
            rows.append({
                "data":          data_str,
                "receita":       round(v["receita"], 2),
                "pedidos":       v["pedidos"],
                "ticket_medio":  v["ticket_medio"],
                "fonte":         "dooki",
                "atualizado_em": agora,
            })

        errors = client.insert_rows_json(table, rows)
        if errors:
            logger.error("[BQ] Erros: %s", errors)
            return 0
        logger.info("[BQ] ✅ %d dias inseridos em %s (fonte=dooki)", len(rows), BQ_TABLE)
        return len(rows)
    except Exception as e:
        logger.error("[BQ] Exceção: %s", e)
        return 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias",    type=int, default=30, help="Dias para sincronizar (padrão: 30)")
    parser.add_argument("--dry-run", action="store_true",  help="Não salva, só exibe")
    args = parser.parse_args()

    hoje      = date.today()
    data_fim  = hoje - timedelta(days=1)   # ontem (dia corrente incompleto)
    data_ini  = hoje - timedelta(days=args.dias)

    logger.info("Buscando pedidos pagos Dooki: %s → %s", data_ini, data_fim)
    pedidos = buscar_pedidos_pagos(data_ini, data_fim)
    logger.info("%d pedidos pagos encontrados", len(pedidos))

    if not pedidos:
        logger.info("Nenhum pedido pago no período.")
        return

    por_dia = agregar_por_dia(pedidos)
    receita_total = sum(v["receita"] for v in por_dia.values())
    logger.info("Receita total paga: R$%.2f | %d dias com dados", receita_total, len(por_dia))

    if args.dry_run:
        print("\n=== DRY RUN ===")
        for d in sorted(por_dia.keys()):
            v = por_dia[d]
            print(f"  {d}: R${v['receita']:,.2f} | {v['pedidos']} pedidos | ticket R${v['ticket_medio']:,.2f}")
        return

    n_sqlite = salvar_sqlite(por_dia)
    logger.info("SQLite: %d dias salvos em %s", n_sqlite, DB_PATH)

    n_bq = salvar_bigquery(por_dia)
    logger.info("BigQuery: %d dias espelhados", n_bq)


if __name__ == "__main__":
    main()
