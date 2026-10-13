"""
shopify/abandoned_checkouts.py

Carrega checkouts abandonados da Shopify API para dois destinos:

  1. Detalhe por checkout (tabela: shopify_abandoned_detail)
     - 1 linha por checkout, com email, valor, UTM completo, data
     - Útil para cruzar com CRM/Klaviyo e análise de recuperação

  2. Agregação diária por UTM source (tabela: shopify_abandoned_daily)
     - 1 linha por (data, utm_source) com contagem e valor total
     - Útil para o relatório de desempenho por canal

Ambas as tabelas são gravadas no SQLite local e espelhadas no BigQuery.

Classificação de UTM source:
  - 'facebook' | 'instagram' | 'fb' | 'meta' → 'meta'
  - 'google' | 'cpc' | 'pmax'               → 'google'
  - 'Klaviyo' | 'email' | 'klaviyo'         → 'email'
  - 'revi' | 'wpp' | 'whatsapp'             → 'whatsapp'
  - utm vazio + referring_site present       → 'organico'
  - sem UTM                                  → 'direto'
  - outros                                   → valor literal do utm_source

Uso:
  python -m shopify.abandoned_checkouts --dias 30 --cliente pod_kombucha
  python -m shopify.abandoned_checkouts --dias 30 --cliente source_of
"""

import argparse
import logging
import os
import sqlite3
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

API_VER = "2024-01"

# ── DDL ──────────────────────────────────────────────────────────────────────

_DDL_DETAIL = """
CREATE TABLE IF NOT EXISTS shopify_abandoned_detail (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    checkout_id     TEXT    NOT NULL,
    data            TEXT    NOT NULL,
    email           TEXT,
    subtotal        REAL    NOT NULL DEFAULT 0,
    utm_source      TEXT,
    utm_medium      TEXT,
    utm_campaign    TEXT,
    utm_source_norm TEXT,    -- fonte normalizada: meta/google/email/whatsapp/organico/direto/outro
    landing_site    TEXT,
    status          TEXT,    -- open | completed | closed
    recuperado      INTEGER NOT NULL DEFAULT 0,  -- 1 se completed_at IS NOT NULL
    UNIQUE(checkout_id)
);
"""

_DDL_DAILY = """
CREATE TABLE IF NOT EXISTS shopify_abandoned_daily (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data            TEXT    NOT NULL,
    utm_source_norm TEXT    NOT NULL,
    checkouts       INTEGER NOT NULL DEFAULT 0,
    subtotal_total  REAL    NOT NULL DEFAULT 0,
    recuperados     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(data, utm_source_norm)
);
"""

_UPSERT_DETAIL = """
INSERT INTO shopify_abandoned_detail
    (checkout_id, data, email, subtotal, utm_source, utm_medium, utm_campaign,
     utm_source_norm, landing_site, status, recuperado)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(checkout_id) DO UPDATE SET
    data            = excluded.data,
    email           = excluded.email,
    subtotal        = excluded.subtotal,
    utm_source      = excluded.utm_source,
    utm_medium      = excluded.utm_medium,
    utm_campaign    = excluded.utm_campaign,
    utm_source_norm = excluded.utm_source_norm,
    landing_site    = excluded.landing_site,
    status          = excluded.status,
    recuperado      = excluded.recuperado
;
"""

_UPSERT_DAILY = """
INSERT INTO shopify_abandoned_daily
    (data, utm_source_norm, checkouts, subtotal_total, recuperados)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(data, utm_source_norm) DO UPDATE SET
    checkouts      = excluded.checkouts,
    subtotal_total = excluded.subtotal_total,
    recuperados    = excluded.recuperados
;
"""


# ── Normalização UTM ──────────────────────────────────────────────────────────

def _normalizar_source(utm_source: str, utm_medium: str, landing_site: str, referring_site: str) -> str:
    src = (utm_source or "").lower()
    med = (utm_medium or "").lower()
    ls  = (landing_site or "").lower()
    ref = (referring_site or "").lower()

    if any(x in src for x in ["facebook", "instagram", "fb", "meta", "ig"]):
        return "meta"
    if any(x in src for x in ["google", "pmax", "shopping"]):
        return "google"
    if med in ("cpc",) and not src:
        # cpc sem source identificável → tenta landing_site
        if "gclid" in ls:
            return "google"
        return "google"
    if any(x in src for x in ["klaviyo", "email", "mail", "crm"]):
        return "email"
    if any(x in src for x in ["revi", "wpp", "whatsapp"]):
        return "whatsapp"
    if not src and not med:
        if ref:
            return "organico"
        return "direto"
    if src:
        return src  # literal
    return "direto"


def _extrair_utm(checkout: dict) -> dict:
    """Extrai UTM do landing_site (querystring) ou note_attributes."""
    ls  = checkout.get("landing_site") or ""
    ref = checkout.get("referring_site") or ""

    # 1. note_attributes têm prioridade (mais confiável)
    notes = {n["name"].lower(): (n.get("value") or "") for n in (checkout.get("note_attributes") or [])}
    src  = notes.get("utm_source") or notes.get("utm_source".replace("_", ""))
    med  = notes.get("utm_medium") or notes.get("utm_medium".replace("_", ""))
    camp = notes.get("utm_campaign") or notes.get("utm_campaign".replace("_", ""))

    # 2. fallback: querystring do landing_site
    if not src and "?" in ls:
        try:
            qs = parse_qs(ls.split("?", 1)[1])
            src  = src  or (qs.get("utm_source",  [""])[0])
            med  = med  or (qs.get("utm_medium",  [""])[0])
            camp = camp or (qs.get("utm_campaign", [""])[0])
        except Exception:
            pass

    norm = _normalizar_source(src or "", med or "", ls, ref)
    return {
        "utm_source":      src or None,
        "utm_medium":      med or None,
        "utm_campaign":    camp or None,
        "utm_source_norm": norm,
    }


# ── Configuração de clientes ──────────────────────────────────────────────────

_CLIENTES = {
    "source_of": {
        "store_env": "SHOPIFY_STORE",
        "token_env": "SHOPIFY_TOKEN",
        "db_path":   "data/performance/source_of/shopify_abandoned.db",
    },
    "pod_kombucha": {
        "store_env": "SHOPIFY_POD_STORE",
        "token_env": "SHOPIFY_POD_TOKEN",
        "db_path":   "data/performance/pod_kombucha/shopify_abandoned.db",
    },
}

BASE_DIR = Path(__file__).parent.parent


# ── API Shopify ───────────────────────────────────────────────────────────────

def _paginar_checkouts(store: str, token: str, data_inicio: date, data_fim: date) -> list[dict]:
    """Busca todos os checkouts (open + completed) no período."""
    url = f"https://{store}.myshopify.com/admin/api/{API_VER}/checkouts.json"
    params = {
        "limit":          250,
        "created_at_min": f"{data_inicio}T00:00:00Z",
        "created_at_max": f"{data_fim}T23:59:59Z",
        "fields": (
            "id,created_at,updated_at,completed_at,email,"
            "subtotal_price,total_discounts,total_price,"
            "landing_site,referring_site,source_name,note_attributes,"
            "status"
        ),
    }
    headers = {"X-Shopify-Access-Token": token}
    all_rows = []
    page = 0

    while url:
        resp = requests.get(url, headers=headers, params=params if page == 0 else {}, timeout=30)
        if resp.status_code == 429:
            logger.warning("[abandoned] Rate limit — aguardando 60s")
            time.sleep(60)
            continue
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("checkouts", [])
        all_rows.extend(batch)
        page += 1

        # Cursor-based pagination
        link_header = resp.headers.get("Link", "")
        next_url = None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                next_url = part.split(";")[0].strip().strip("<>")
                break
        url = next_url
        params = {}

        if batch:
            logger.info("[abandoned/%s] Página %d: %d checkouts", store[:10], page, len(batch))

    logger.info("[abandoned/%s] Total: %d checkouts (%s → %s)", store[:10], len(all_rows), data_inicio, data_fim)
    return all_rows


# ── Persistência ──────────────────────────────────────────────────────────────

def _salvar_sqlite(checkouts: list[dict], db_path: Path) -> tuple[int, int]:
    """Salva detalhe + agregação diária. Retorna (linhas_detalhe, dias_diario)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_DDL_DETAIL)
    conn.execute(_DDL_DAILY)

    # Acumuladores para daily
    daily_count:    dict[tuple, int]   = defaultdict(int)
    daily_subtotal: dict[tuple, float] = defaultdict(float)
    daily_recup:    dict[tuple, int]   = defaultdict(int)

    n_detail = 0
    for ck in checkouts:
        created = (ck.get("created_at") or "")[:10]
        if not created:
            continue

        subtotal   = float(ck.get("subtotal_price") or 0)
        recuperado = 1 if ck.get("completed_at") else 0
        utms       = _extrair_utm(ck)
        status     = "completed" if recuperado else "open"

        conn.execute(_UPSERT_DETAIL, (
            str(ck["id"]), created,
            ck.get("email"), subtotal,
            utms["utm_source"], utms["utm_medium"], utms["utm_campaign"],
            utms["utm_source_norm"],
            (ck.get("landing_site") or "")[:500],
            status, recuperado,
        ))
        n_detail += 1

        key = (created, utms["utm_source_norm"])
        daily_count[key]    += 1
        daily_subtotal[key] += subtotal
        daily_recup[key]    += recuperado

    for (dia, src), cnt in daily_count.items():
        conn.execute(_UPSERT_DAILY, (dia, src, cnt, round(daily_subtotal[(dia, src)], 2), daily_recup[(dia, src)]))

    conn.commit()
    conn.close()
    return n_detail, len(daily_count)


# ── BigQuery ──────────────────────────────────────────────────────────────────

_BQ_DDL_DETAIL = {
    "abandoned_checkout_detail": [
        ("checkout_id",     "STRING"),
        ("cliente",         "STRING"),
        ("data",            "DATE"),
        ("email",           "STRING"),
        ("subtotal",        "NUMERIC"),
        ("utm_source",      "STRING"),
        ("utm_medium",      "STRING"),
        ("utm_campaign",    "STRING"),
        ("utm_source_norm", "STRING"),
        ("landing_site",    "STRING"),
        ("status",          "STRING"),
        ("recuperado",      "INTEGER"),
        ("criado_em",       "TIMESTAMP"),
    ]
}

_BQ_DDL_DAILY = {
    "abandoned_checkout_daily": [
        ("cliente",         "STRING"),
        ("data",            "DATE"),
        ("utm_source_norm", "STRING"),
        ("checkouts",       "INTEGER"),
        ("subtotal_total",  "NUMERIC"),
        ("recuperados",     "INTEGER"),
        ("criado_em",       "TIMESTAMP"),
    ]
}


def _garantir_tabela_bq(client, project: str, dataset: str, table_name: str, schema_cols: list) -> None:
    """Cria a tabela BQ se não existir."""
    from google.cloud import bigquery
    tab_ref = f"{project}.{dataset}.{table_name}"
    try:
        client.get_table(tab_ref)
    except Exception:
        schema = [bigquery.SchemaField(name, ftype) for name, ftype in schema_cols]
        table  = bigquery.Table(tab_ref, schema=schema)
        client.create_table(table)
        logger.info("[BQ] Tabela %s criada", table_name)


def _escrever_bq(checkouts: list[dict], cliente_key: str) -> None:
    """Escreve detalhe + daily no BigQuery via MERGE."""
    try:
        from db.bq_writer import _client, _insert
        import os
        client  = _client()
        project = os.environ["BQ_PROJECT"]
        dataset = os.environ["BQ_DATASET"]

        for tname, schema in {**_BQ_DDL_DETAIL, **_BQ_DDL_DAILY}.items():
            _garantir_tabela_bq(client, project, dataset, tname, schema)

        from datetime import datetime as dt
        agora = dt.utcnow().isoformat()

        # Detalhe
        detail_rows = []
        daily_agg: dict[tuple, dict] = {}

        for ck in checkouts:
            created = (ck.get("created_at") or "")[:10]
            if not created:
                continue
            subtotal   = float(ck.get("subtotal_price") or 0)
            recuperado = 1 if ck.get("completed_at") else 0
            utms       = _extrair_utm(ck)

            detail_rows.append({
                "checkout_id":     str(ck["id"]),
                "cliente":         cliente_key,
                "data":            created,
                "email":           ck.get("email"),
                "subtotal":        str(round(subtotal, 2)),
                "utm_source":      utms["utm_source"],
                "utm_medium":      utms["utm_medium"],
                "utm_campaign":    utms["utm_campaign"],
                "utm_source_norm": utms["utm_source_norm"],
                "landing_site":    (ck.get("landing_site") or "")[:500],
                "status":          "completed" if recuperado else "open",
                "recuperado":      recuperado,
                "criado_em":       agora,
            })

            key = (created, utms["utm_source_norm"])
            if key not in daily_agg:
                daily_agg[key] = {"checkouts": 0, "subtotal_total": 0.0, "recuperados": 0}
            daily_agg[key]["checkouts"]     += 1
            daily_agg[key]["subtotal_total"] += subtotal
            daily_agg[key]["recuperados"]    += recuperado

        daily_rows = [
            {
                "cliente":         cliente_key,
                "data":            dia,
                "utm_source_norm": src,
                "checkouts":       v["checkouts"],
                "subtotal_total":  str(round(v["subtotal_total"], 2)),
                "recuperados":     v["recuperados"],
                "criado_em":       agora,
            }
            for (dia, src), v in daily_agg.items()
        ]

        # MERGE via _insert (usa MERGE interno com chave por tabela)
        if detail_rows:
            _insert("abandoned_checkout_detail", detail_rows, f"abandoned_detail/{cliente_key}")

        if daily_rows:
            _insert("abandoned_checkout_daily", daily_rows, f"abandoned_daily/{cliente_key}")

        logger.info("[BQ] abandoned %s: %d detalhe, %d daily", cliente_key, len(detail_rows), len(daily_rows))

    except Exception as e:
        logger.warning("[BQ abandoned] falha silenciosa: %s", e)


# ── Entry point ───────────────────────────────────────────────────────────────

def carregar_abandoned(dias: int = 30, cliente_key: str = "pod_kombucha") -> dict:
    """
    Busca checkouts abandonados e salva em SQLite + BQ.

    Returns:
        {"total": int, "dias_diario": int, "periodo": str}
    """
    cfg = _CLIENTES.get(cliente_key)
    if not cfg:
        raise ValueError(f"cliente_key inválido: {cliente_key}. Disponíveis: {list(_CLIENTES)}")

    store = os.environ.get(cfg["store_env"], "")
    token = os.environ.get(cfg["token_env"], "")
    if not store or not token:
        raise EnvironmentError(f"Env vars {cfg['store_env']}/{cfg['token_env']} não definidas")

    data_fim   = date.today()
    data_inicio = data_fim - timedelta(days=dias - 1)

    checkouts = _paginar_checkouts(store, token, data_inicio, data_fim)

    db_path = BASE_DIR / cfg["db_path"]
    n_detail, n_daily = _salvar_sqlite(checkouts, db_path)
    logger.info("[abandoned/%s] SQLite: %d detalhe, %d diário", cliente_key, n_detail, n_daily)

    _escrever_bq(checkouts, cliente_key)

    return {
        "total":      n_detail,
        "dias_diario": n_daily,
        "periodo":    f"{data_inicio} → {data_fim}",
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Shopify Abandoned Checkouts → SQLite + BQ")
    parser.add_argument("--dias",    type=int, default=30)
    parser.add_argument("--cliente", default="pod_kombucha", choices=list(_CLIENTES))
    args = parser.parse_args()

    resultado = carregar_abandoned(dias=args.dias, cliente_key=args.cliente)
    print(f"✅ {args.cliente} | {resultado['total']} checkouts | {resultado['dias_diario']} dias | {resultado['periodo']}")
