"""
dooki/sync.py

Sincroniza pedidos PAGOS das lojas Yampi/Dooki da POD Kombucha para:
  - SQLite local (tabela: dooki_pedidos_diario, por loja)
  - BigQuery (guara-db.guara_performance.dooki_pedidos_diario — coluna
    fonte='dooki_<loja>', uma loja por linha/dia)

A POD Kombucha vende em 3 lojas Yampi/Dooki separadas (poddi, pod_kombucha,
pod8) que precisam ser somadas no gold, mas mantidas distintas no bronze
(coluna `fonte`) para poder auditar cada loja isoladamente.

Regra de receita paga:
  Apenas pedidos com status.alias = 'paid' são contabilizados como receita.
  Pedidos 'invoiced', 'on_carriage', 'cancelled' etc. são ignorados no totalizador.

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
BQ_TABLE   = "dooki_pedidos_diario"   # tabela dedicada Dooki no BQ (uma linha por data+loja)

# Lojas Yampi/Dooki da POD Kombucha. Cada uma tem credenciais próprias.
# `fonte` é o identificador gravado no BQ/SQLite para distinguir a loja.
LOJAS = [
    {
        "nome":        "poddi",
        "fonte":       "dooki_poddi",
        "base_url_env": "DOOKI_PODDI_BASE_URL",
        "token_env":    "DOOKI_PODDI_USER_TOKEN",
        "secret_env":   "DOOKI_PODDI_USER_SECRET_KEY",
    },
    {
        "nome":        "pod_kombucha",
        "fonte":       "dooki_pod_kombucha",
        "base_url_env": "DOOKI_POD_KOMBUCHA_BASE_URL",
        "token_env":    "DOOKI_POD_KOMBUCHA_USER_TOKEN",
        "secret_env":   "DOOKI_POD_KOMBUCHA_USER_SECRET_KEY",
    },
    {
        "nome":        "pod8",
        "fonte":       "dooki_pod8",
        "base_url_env": "DOOKI_POD8_BASE_URL",
        # pod8 usa o mesmo token/secret da loja poddi (mesma conta Dooki/Yampi)
        "token_env":    "DOOKI_PODDI_USER_TOKEN",
        "secret_env":   "DOOKI_PODDI_USER_SECRET_KEY",
    },
]

# Apenas estes status contam como receita paga
STATUS_RECEITA_PAGA = {"paid"}

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


def buscar_pedidos_pagos(base_url: str, token: str, secret: str,
                          data_inicio: date, data_fim: date) -> list[dict]:
    """
    Retorna pedidos com status 'paid' no intervalo [data_inicio, data_fim]
    para a loja identificada por `base_url`/`token`/`secret`.

    A API Dooki retorna pedidos em ordem DESC (mais recente primeiro, pág 1).
    Vai até pág 99 no máximo (400 acima disso).
    Paginamos da pág 1 em diante e paramos quando a data sair do período.
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
    fonte           TEXT NOT NULL,
    atualizado_em   TEXT NOT NULL,
    UNIQUE(data, fonte)
)
"""


def salvar_sqlite(por_dia: dict[str, dict], fonte: str) -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(CREATE_TABLE)
    agora = datetime.now(timezone.utc).isoformat()
    n = 0
    for data_str, v in por_dia.items():
        conn.execute("""
            INSERT INTO dooki_pedidos_diario
              (data, receita, pedidos, ticket_medio, clientes_unicos, fonte, atualizado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(data, fonte) DO UPDATE SET
              receita         = excluded.receita,
              pedidos         = excluded.pedidos,
              ticket_medio    = excluded.ticket_medio,
              clientes_unicos = excluded.clientes_unicos,
              atualizado_em   = excluded.atualizado_em
        """, (data_str, round(v["receita"], 2), v["pedidos"],
              v["ticket_medio"], v["clientes_unicos"], fonte, agora))
        n += 1
    conn.commit()
    conn.close()
    return n


# ── BigQuery ──────────────────────────────────────────────────────────────────

def salvar_bigquery(client: bq.Client, por_dia: dict[str, dict], fonte: str) -> int:
    """
    Upsert idempotente por (data, fonte) via MERGE — evita duplicar linhas
    quando o sync roda mais de uma vez para o mesmo dia/loja.
    """
    if not por_dia:
        return 0

    agora = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "data":          data_str,
            "receita":       round(v["receita"], 2),
            "pedidos":       v["pedidos"],
            "ticket_medio":  v["ticket_medio"],
            "fonte":         fonte,
            "atualizado_em": agora,
        }
        for data_str, v in por_dia.items()
    ]

    try:
        staging_id = f"{BQ_PROJECT}.{BQ_DATASET}._staging_dooki_{fonte}"
        job_config = bq.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE",
            schema=[
                bq.SchemaField("data", "DATE"),
                bq.SchemaField("receita", "NUMERIC"),
                bq.SchemaField("pedidos", "INTEGER"),
                bq.SchemaField("ticket_medio", "NUMERIC"),
                bq.SchemaField("fonte", "STRING"),
                bq.SchemaField("atualizado_em", "TIMESTAMP"),
            ],
        )
        client.load_table_from_json(rows, staging_id, job_config=job_config).result()

        # A API da Dooki, numa sincronização incremental, pode devolver só os
        # pedidos "recém atualizados" pra uma data antiga que já tinha mais
        # pedidos completos (ex: paginação por data de atualização, não de
        # criação). Só sobrescreve se o novo total for >= o que já tínhamos —
        # nunca encolhe um dia.
        merge_sql = f"""
        MERGE `{BQ_PROJECT}.{BQ_DATASET}.{BQ_TABLE}` T
        USING `{staging_id}` S
        ON T.data = S.data AND T.fonte = S.fonte
        WHEN MATCHED AND S.pedidos >= T.pedidos THEN
          UPDATE SET receita = S.receita, pedidos = S.pedidos,
                     ticket_medio = S.ticket_medio, atualizado_em = S.atualizado_em
        WHEN NOT MATCHED THEN
          INSERT (data, receita, pedidos, ticket_medio, fonte, atualizado_em)
          VALUES (S.data, S.receita, S.pedidos, S.ticket_medio, S.fonte, S.atualizado_em)
        """
        client.query(merge_sql).result()
        client.delete_table(staging_id, not_found_ok=True)

        logger.info("[BQ] ✅ %d dias upsert em %s (fonte=%s)", len(rows), BQ_TABLE, fonte)
        return len(rows)
    except Exception as e:
        logger.error("[BQ] Exceção (fonte=%s): %s", fonte, e)
        return 0


# ── Main ──────────────────────────────────────────────────────────────────────

def sync_loja(client: bq.Client | None, loja: dict, data_ini: date, data_fim: date,
              dry_run: bool) -> None:
    nome   = loja["nome"]
    fonte  = loja["fonte"]
    base_url = os.getenv(loja["base_url_env"], "").rstrip("/")
    token    = os.getenv(loja["token_env"], "")
    secret   = os.getenv(loja["secret_env"], "")

    if not base_url or not token or not secret:
        logger.warning("Loja '%s' sem credenciais configuradas (%s/%s/%s) — pulando",
                        nome, loja["base_url_env"], loja["token_env"], loja["secret_env"])
        return

    logger.info("[%s] Buscando pedidos pagos: %s → %s", nome, data_ini, data_fim)
    pedidos = buscar_pedidos_pagos(base_url, token, secret, data_ini, data_fim)
    logger.info("[%s] %d pedidos pagos encontrados", nome, len(pedidos))

    if not pedidos:
        logger.info("[%s] Nenhum pedido pago no período.", nome)
        return

    por_dia = agregar_por_dia(pedidos)
    receita_total = sum(v["receita"] for v in por_dia.values())
    logger.info("[%s] Receita total paga: R$%.2f | %d dias com dados", nome, receita_total, len(por_dia))

    if dry_run:
        print(f"\n=== DRY RUN ({nome}) ===")
        for d in sorted(por_dia.keys()):
            v = por_dia[d]
            print(f"  {d}: R${v['receita']:,.2f} | {v['pedidos']} pedidos | ticket R${v['ticket_medio']:,.2f}")
        return

    n_sqlite = salvar_sqlite(por_dia, fonte)
    logger.info("[%s] SQLite: %d dias salvos em %s", nome, n_sqlite, DB_PATH)

    if client is not None:
        n_bq = salvar_bigquery(client, por_dia, fonte)
        logger.info("[%s] BigQuery: %d dias espelhados", nome, n_bq)


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
