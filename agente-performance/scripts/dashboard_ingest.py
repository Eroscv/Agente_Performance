"""
dashboard_ingest.py — Envia dados de performance para a API do dashboard (Supabase).

Fontes:
  - Meta Ads      → data/performance/source_of/meta_ads.db
  - Google Ads    → data/performance/source_of/google_ads.db
  - Shopify       → data/performance/source_of/shopify_receita.db

Sync incremental:
  - Grava last_sync_at por (cliente, plataforma) em data/dashboard_sync.db
  - Na próxima execução, envia apenas datas > last_sync_at

Uso:
  python dashboard_ingest.py                  # roda todas as plataformas
  python dashboard_ingest.py --force          # ignora last_sync_at, reenvia tudo
  python dashboard_ingest.py --dias 7         # últimos N dias (override)
  python dashboard_ingest.py --dry-run        # mostra payload sem enviar
"""

import argparse
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ============================================================
# CONFIG
# ============================================================

BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR / "data" / "performance" / "source_of"
DATA_DIR_POD  = BASE_DIR / "data" / "performance" / "pod_kombucha"
SYNC_DB_PATH  = BASE_DIR / "data" / "dashboard_sync.db"

DASHBOARD_URL = os.getenv(
    "DASHBOARD_INGEST_URL",
    "https://ecinffgoycqvktmrmvaz.supabase.co/functions/v1/api-dashboard-ingest"
)
DASHBOARD_TOKEN = os.getenv("DASHBOARD_INGEST_TOKEN", "")

RETRY_MAX       = 3
RETRY_BASE_SECS = 2

# ============================================================
# SYNC STATE DB
# ============================================================

@contextmanager
def _sync_db():
    conn = sqlite3.connect(str(SYNC_DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            cliente    TEXT NOT NULL,
            plataforma TEXT NOT NULL,
            last_sync  TEXT NOT NULL,
            PRIMARY KEY (cliente, plataforma)
        )
    """)
    conn.commit()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_last_sync(cliente: str, plataforma: str) -> str | None:
    with _sync_db() as conn:
        row = conn.execute(
            "SELECT last_sync FROM sync_state WHERE cliente=? AND plataforma=?",
            (cliente, plataforma)
        ).fetchone()
    return row[0] if row else None


def set_last_sync(cliente: str, plataforma: str, ts: str) -> None:
    with _sync_db() as conn:
        conn.execute(
            "INSERT INTO sync_state (cliente, plataforma, last_sync) VALUES (?,?,?)"
            " ON CONFLICT(cliente, plataforma) DO UPDATE SET last_sync=excluded.last_sync",
            (cliente, plataforma, ts)
        )


# ============================================================
# HELPERS
# ============================================================

def _query(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    if not db_path.exists():
        log.warning("DB não encontrado: %s", db_path)
        return []
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _post(payload: dict, dry_run: bool) -> bool:
    if dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return True

    headers = {
        "Authorization": f"Bearer {DASHBOARD_TOKEN}",
        "Content-Type": "application/json",
    }
    for attempt in range(1, RETRY_MAX + 1):
        try:
            resp = requests.post(DASHBOARD_URL, json=payload, headers=headers, timeout=30)
            if resp.ok:
                log.info("✅ POST OK [%s] %s/%s (%dms)",
                         resp.status_code,
                         payload["meta"]["cliente"],
                         payload["meta"]["plataforma"],
                         int(resp.elapsed.total_seconds() * 1000))
                return True
            if resp.status_code in {429, 500, 502, 503} and attempt < RETRY_MAX:
                wait = RETRY_BASE_SECS * (2 ** (attempt - 1))
                log.warning("HTTP %s — tentativa %d/%d, aguardando %ds",
                            resp.status_code, attempt, RETRY_MAX, wait)
                time.sleep(wait)
                continue
            log.error("❌ HTTP %s: %s", resp.status_code, resp.text[:200])
            return False
        except requests.RequestException as exc:
            if attempt < RETRY_MAX:
                time.sleep(RETRY_BASE_SECS * (2 ** (attempt - 1)))
            else:
                log.error("❌ Conexão falhou: %s", exc)
                return False
    return False


def _kpi_mensal(db, table: str, col_invest: str = "investimento",
               col_receita: str = "valor_compras", col_compras: str = "compras") -> dict:
    """Calcula KPI do mês corrente completo (1º dia do mês até hoje)."""
    mes_inicio = datetime.now(timezone.utc).strftime("%Y-%m-01")
    rows = _query(db, f"""
        SELECT
            ROUND(SUM({col_invest}), 2)  AS investimento,
            ROUND(SUM({col_receita}), 2) AS receita,
            CAST(SUM({col_compras}) AS INTEGER) AS compras,
            MIN(data) AS period_start,
            MAX(data) AS period_end
        FROM {table}
        WHERE data >= ?
    """, (mes_inicio,))
    r = rows[0] if rows else {}
    inv   = r.get("investimento") or 0.0
    rec   = r.get("receita")      or 0.0
    comp  = r.get("compras")      or 0
    return {
        "investimento":  inv,
        "receita":       rec,
        "compras":       comp,
        "roas":          round(rec / inv, 2) if inv > 0 else 0.0,
        "cpa":           round(inv / comp, 2) if comp > 0 else 0.0,
        "period_start":  r.get("period_start") or mes_inicio,
        "period_end":    r.get("period_end")   or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


# ============================================================
# META ADS — Source OF
# ============================================================

def build_meta_source_of(desde: str) -> dict | None:
    db = DATA_DIR / "meta_ads.db"
    table = "source_of_meta_ads_diario"

    # Series diárias (slice incremental — para upsert no Supabase)
    series_rows = _query(db, f"""
        SELECT
            data                                  AS date,
            ROUND(SUM(investimento), 2)           AS investimento,
            ROUND(SUM(valor_compras), 2)          AS receita,
            CAST(SUM(compras) AS INTEGER)         AS compras
        FROM {table}
        WHERE data >= ?
        GROUP BY data
        ORDER BY data
    """, (desde,))

    if not series_rows:
        log.info("[Meta/SourceOF] Nenhum dado novo desde %s", desde)
        return None

    # ROAS por data (receita/investimento)
    for row in series_rows:
        inv = row["investimento"] or 0
        rec = row["receita"] or 0
        row["roas"] = round(rec / inv, 2) if inv > 0 else 0.0

    # KPI do mês corrente completo (independente do slice incremental)
    kpi_m = _kpi_mensal(db, table, col_receita="valor_compras")
    total_inv  = kpi_m["investimento"]
    total_rec  = kpi_m["receita"]
    total_comp = kpi_m["compras"]
    roas_geral = kpi_m["roas"]
    cpa        = kpi_m["cpa"]
    kpi_period_start = kpi_m["period_start"]
    kpi_period_end   = kpi_m["period_end"]

    # Variação ROAS (último dia vs penúltimo do slice)
    variacao_roas = 0.0
    if len(series_rows) >= 2:
        r_ant = series_rows[-2]["roas"]
        r_ult = series_rows[-1]["roas"]
        variacao_roas = round((r_ult - r_ant) / r_ant * 100, 1) if r_ant > 0 else 0.0

    # Campanhas (último dia disponível)
    ultimo_dia = series_rows[-1]["date"]
    camp_rows = _query(db, f"""
        SELECT
            campanha                              AS nome,
            ROUND(SUM(roas_compras), 2)           AS roas,
            ROUND(SUM(investimento), 2)           AS invest,
            CAST(SUM(compras) AS INTEGER)         AS compras,
            status_criativo                       AS status_raw
        FROM {table}
        WHERE data = ?
        GROUP BY campanha
        ORDER BY invest DESC
        LIMIT 20
    """, (ultimo_dia,))

    campanhas = []
    for c in camp_rows:
        roas_c = c["roas"] or 0.0
        campanhas.append({
            "nome":    c["nome"] or "—",
            "roas":    roas_c,
            "invest":  c["invest"] or 0.0,
            "compras": c["compras"] or 0,
            "status":  "ativa" if roas_c >= 1.0 else "alerta",
        })

    return {
        "meta": {"cliente": "Source OF", "plataforma": "meta_ads"},
        "series": [
            {
                "date":        r["date"],
                "roas":        r["roas"],
                "investimento": r["investimento"],
                "receita":     r["receita"],
                "compras":     r["compras"],
            }
            for r in series_rows
        ],
        "kpi": {
            "roas":            roas_geral,
            "investimento":    round(total_inv, 2),
            "receita":         round(total_rec, 2),
            "compras":         total_comp,
            "cpa":             cpa,
            "variacao_roas":   variacao_roas,
            "period_start":    kpi_period_start,
            "period_end":      kpi_period_end,
        },
        "campanhas": campanhas,
    }


# ============================================================
# GOOGLE ADS — Source OF
# ============================================================

def build_google_source_of(desde: str) -> dict | None:
    db = DATA_DIR / "google_ads.db"
    table = "source_of_gads_diario"

    series_rows = _query(db, f"""
        SELECT
            data                                  AS date,
            ROUND(SUM(investimento), 2)           AS investimento,
            ROUND(SUM(valor_conversao), 2)        AS receita,
            CAST(SUM(conversoes) AS INTEGER)      AS compras
        FROM {table}
        WHERE data >= ?
        GROUP BY data
        ORDER BY data
    """, (desde,))

    if not series_rows:
        log.info("[Google/SourceOF] Nenhum dado novo desde %s", desde)
        return None

    for row in series_rows:
        inv = row["investimento"] or 0
        rec = row["receita"] or 0
        row["roas"] = round(rec / inv, 2) if inv > 0 else 0.0

    kpi_m = _kpi_mensal(db, table, col_receita="valor_conversao", col_compras="conversoes")
    total_inv  = kpi_m["investimento"]
    total_rec  = kpi_m["receita"]
    total_comp = kpi_m["compras"]
    roas_geral = kpi_m["roas"]
    cpa        = kpi_m["cpa"]
    kpi_period_start = kpi_m["period_start"]
    kpi_period_end   = kpi_m["period_end"]

    variacao_roas = 0.0
    if len(series_rows) >= 2:
        r_ant = series_rows[-2]["roas"]
        r_ult = series_rows[-1]["roas"]
        variacao_roas = round((r_ult - r_ant) / r_ant * 100, 1) if r_ant > 0 else 0.0

    ultimo_dia = series_rows[-1]["date"]
    camp_rows = _query(db, f"""
        SELECT
            campanha                              AS nome,
            ROUND(SUM(valor_conversao)/NULLIF(SUM(investimento),0), 2) AS roas,
            ROUND(SUM(investimento), 2)           AS invest,
            CAST(SUM(conversoes) AS INTEGER)      AS compras,
            status
        FROM {table}
        WHERE data = ?
        GROUP BY campanha
        ORDER BY invest DESC
        LIMIT 20
    """, (ultimo_dia,))

    campanhas = []
    for c in camp_rows:
        roas_c = c["roas"] or 0.0
        campanhas.append({
            "nome":    c["nome"] or "—",
            "roas":    roas_c,
            "invest":  c["invest"] or 0.0,
            "compras": c["compras"] or 0,
            "status":  "ativa" if (c["status"] or "").upper() == "ENABLED" and roas_c >= 1.0 else "alerta",
        })

    return {
        "meta": {"cliente": "Source OF", "plataforma": "google_ads"},
        "series": [
            {
                "date":         r["date"],
                "roas":         r["roas"],
                "investimento": r["investimento"],
                "receita":      r["receita"],
                "compras":      r["compras"],
            }
            for r in series_rows
        ],
        "kpi": {
            "roas":          roas_geral,
            "investimento":  round(total_inv, 2),
            "receita":       round(total_rec, 2),
            "compras":       total_comp,
            "cpa":           cpa,
            "variacao_roas": variacao_roas,
            "period_start":  kpi_period_start,
            "period_end":    kpi_period_end,
        },
        "campanhas": campanhas,
    }


# ============================================================
# SHOPIFY — Source OF
# ============================================================

def build_shopify_source_of(desde: str) -> dict | None:
    db = DATA_DIR / "shopify_receita.db"

    rows = _query(db, """
        SELECT
            data,
            ROUND(receita, 2)       AS receita,
            pedidos,
            novos_clientes,
            recompradores,
            ROUND(ticket_medio, 2)  AS ticket_medio
        FROM shopify_receita_diaria
        WHERE data >= ?
        ORDER BY data
    """, (desde,))

    if not rows:
        log.info("[Shopify/SourceOF] Nenhum dado novo desde %s", desde)
        return None

    total_rec    = sum(r["receita"] for r in rows)
    total_ped    = sum(r["pedidos"] for r in rows)
    total_novos  = sum(r["novos_clientes"] for r in rows)
    ticket_medio = round(total_rec / total_ped, 2) if total_ped > 0 else 0.0

    # Variação receita (último vs penúltimo dia)
    variacao_receita = 0.0
    if len(rows) >= 2:
        r_ant = rows[-2]["receita"]
        r_ult = rows[-1]["receita"]
        variacao_receita = round((r_ult - r_ant) / r_ant * 100, 1) if r_ant > 0 else 0.0

    return {
        "meta": {"cliente": "Source OF", "plataforma": "shopify"},
        "series": [
            {
                "date":          r["data"],
                "receita":       r["receita"],
                "pedidos":       r["pedidos"],
                "novos_clientes": r["novos_clientes"],
                "recompradores": r["recompradores"],
                "ticket_medio":  r["ticket_medio"],
            }
            for r in rows
        ],
        "kpi": {
            "receita":           round(total_rec, 2),
            "pedidos":           total_ped,
            "novos_clientes":    total_novos,
            "ticket_medio":      ticket_medio,
            "variacao_receita":  variacao_receita,
            "period_start":      rows[0]["data"],
            "period_end":        rows[-1]["data"],
        },
        "campanhas": [],
    }


# ============================================================
# META ADS — POD Kombucha
# ============================================================

def build_meta_pod(desde: str) -> dict | None:
    db    = DATA_DIR_POD / "meta_ads.db"
    table = "pod_kombucha_meta_ads_diario"

    series_rows = _query(db, f"""
        SELECT
            data                                  AS date,
            ROUND(SUM(investimento), 2)           AS investimento,
            ROUND(SUM(valor_compras), 2)          AS receita,
            CAST(SUM(compras) AS INTEGER)         AS compras
        FROM {table}
        WHERE data >= ?
        GROUP BY data
        ORDER BY data
    """, (desde,))

    if not series_rows:
        log.info("[Meta/POD] Nenhum dado novo desde %s", desde)
        return None

    for row in series_rows:
        inv = row["investimento"] or 0
        rec = row["receita"] or 0
        row["roas"] = round(rec / inv, 2) if inv > 0 else 0.0

    kpi_m = _kpi_mensal(db, table, col_receita="valor_compras")
    total_inv  = kpi_m["investimento"]
    total_rec  = kpi_m["receita"]
    total_comp = kpi_m["compras"]
    roas_geral = kpi_m["roas"]
    cpa        = kpi_m["cpa"]
    kpi_period_start = kpi_m["period_start"]
    kpi_period_end   = kpi_m["period_end"]

    variacao_roas = 0.0
    if len(series_rows) >= 2:
        r_ant = series_rows[-2]["roas"]
        r_ult = series_rows[-1]["roas"]
        variacao_roas = round((r_ult - r_ant) / r_ant * 100, 1) if r_ant > 0 else 0.0

    ultimo_dia = series_rows[-1]["date"]
    camp_rows  = _query(db, f"""
        SELECT
            campanha                              AS nome,
            ROUND(SUM(roas_compras), 2)           AS roas,
            ROUND(SUM(investimento), 2)           AS invest,
            CAST(SUM(compras) AS INTEGER)         AS compras
        FROM {table}
        WHERE data = ?
        GROUP BY campanha
        ORDER BY invest DESC
        LIMIT 20
    """, (ultimo_dia,))

    campanhas = []
    for c in camp_rows:
        roas_c = c["roas"] or 0.0
        campanhas.append({
            "nome":    c["nome"] or "—",
            "roas":    roas_c,
            "invest":  c["invest"] or 0.0,
            "compras": c["compras"] or 0,
            "status":  "ativa" if roas_c >= 1.0 else "alerta",
        })

    return {
        "meta": {"cliente": "POD Kombucha", "plataforma": "meta_ads"},
        "series": [
            {
                "date":         r["date"],
                "roas":         r["roas"],
                "investimento": r["investimento"],
                "receita":      r["receita"],
                "compras":      r["compras"],
            }
            for r in series_rows
        ],
        "kpi": {
            "roas":          roas_geral,
            "investimento":  round(total_inv, 2),
            "receita":       round(total_rec, 2),
            "compras":       total_comp,
            "cpa":           cpa,
            "variacao_roas": variacao_roas,
            "period_start":  kpi_period_start,
            "period_end":    kpi_period_end,
        },
        "campanhas": campanhas,
    }


# ============================================================
# GOOGLE ADS — POD Kombucha
# ============================================================

def build_google_pod(desde: str) -> dict | None:
    db    = DATA_DIR_POD / "google_ads.db"
    table = "pod_kombucha_gads_diario"

    series_rows = _query(db, f"""
        SELECT
            data                                  AS date,
            ROUND(SUM(investimento), 2)           AS investimento,
            ROUND(SUM(valor_conversao), 2)        AS receita,
            CAST(SUM(conversoes) AS INTEGER)      AS compras
        FROM {table}
        WHERE data >= ?
        GROUP BY data
        ORDER BY data
    """, (desde,))

    if not series_rows:
        log.info("[Google/POD] Nenhum dado novo desde %s", desde)
        return None

    for row in series_rows:
        inv = row["investimento"] or 0
        rec = row["receita"] or 0
        row["roas"] = round(rec / inv, 2) if inv > 0 else 0.0

    kpi_m = _kpi_mensal(db, table, col_receita="valor_conversao", col_compras="conversoes")
    total_inv  = kpi_m["investimento"]
    total_rec  = kpi_m["receita"]
    total_comp = kpi_m["compras"]
    roas_geral = kpi_m["roas"]
    cpa        = kpi_m["cpa"]
    kpi_period_start = kpi_m["period_start"]
    kpi_period_end   = kpi_m["period_end"]

    variacao_roas = 0.0
    if len(series_rows) >= 2:
        r_ant = series_rows[-2]["roas"]
        r_ult = series_rows[-1]["roas"]
        variacao_roas = round((r_ult - r_ant) / r_ant * 100, 1) if r_ant > 0 else 0.0

    ultimo_dia = series_rows[-1]["date"]
    camp_rows  = _query(db, f"""
        SELECT
            campanha                              AS nome,
            ROUND(SUM(valor_conversao)/NULLIF(SUM(investimento),0), 2) AS roas,
            ROUND(SUM(investimento), 2)           AS invest,
            CAST(SUM(conversoes) AS INTEGER)      AS compras
        FROM {table}
        WHERE data = ?
        GROUP BY campanha
        ORDER BY invest DESC
        LIMIT 20
    """, (ultimo_dia,))

    campanhas = []
    for c in camp_rows:
        roas_c = c["roas"] or 0.0
        campanhas.append({
            "nome":    c["nome"] or "—",
            "roas":    roas_c,
            "invest":  c["invest"] or 0.0,
            "compras": c["compras"] or 0,
            "status":  "ativa" if roas_c >= 1.0 else "alerta",
        })

    return {
        "meta": {"cliente": "POD Kombucha", "plataforma": "google_ads"},
        "series": [
            {
                "date":         r["date"],
                "roas":         r["roas"],
                "investimento": r["investimento"],
                "receita":      r["receita"],
                "compras":      r["compras"],
            }
            for r in series_rows
        ],
        "kpi": {
            "roas":          roas_geral,
            "investimento":  round(total_inv, 2),
            "receita":       round(total_rec, 2),
            "compras":       total_comp,
            "cpa":           cpa,
            "variacao_roas": variacao_roas,
            "period_start":  kpi_period_start,
            "period_end":    kpi_period_end,
        },
        "campanhas": campanhas,
    }


# ============================================================
# ORQUESTRADOR
# ============================================================

PLATAFORMAS = {
    "meta_ads":       ("Source OF",    build_meta_source_of),
    "google_ads":     ("Source OF",    build_google_source_of),
    "shopify":        ("Source OF",    build_shopify_source_of),
    "pod_meta_ads":   ("POD Kombucha", build_meta_pod),
    "pod_google_ads": ("POD Kombucha", build_google_pod),
}

DEFAULT_LOOKBACK_DAYS = 30  # primeira sync: últimos 30 dias


def _desde_para(cliente: str, plataforma: str, force: bool, dias: int | None) -> str:
    if force or dias:
        n = dias or DEFAULT_LOOKBACK_DAYS
        return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")

    last = get_last_sync(cliente, plataforma)
    if last:
        # Re-envia desde 2 dias antes do last_sync (janela de segurança para atualizações tardias)
        dt = datetime.fromisoformat(last) - timedelta(days=2)
        return dt.strftime("%Y-%m-%d")

    # Primeira vez: últimos 30 dias
    return (datetime.now(timezone.utc) - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")


def run(force: bool = False, dias: int | None = None, dry_run: bool = False,
        plataformas: list[str] | None = None) -> None:

    targets = plataformas or list(PLATAFORMAS.keys())
    agora   = datetime.now(timezone.utc).isoformat()
    ok_count = 0
    skip_count = 0
    err_count = 0

    for plat in targets:
        if plat not in PLATAFORMAS:
            log.warning("Plataforma desconhecida: %s", plat)
            continue

        cliente, builder = PLATAFORMAS[plat]
        desde = _desde_para(cliente, plat, force, dias)

        log.info("📊 %s / %s | desde=%s", cliente, plat, desde)

        payload = builder(desde)
        if payload is None:
            skip_count += 1
            continue

        ok = _post(payload, dry_run)
        if ok:
            ok_count += 1
            if not dry_run:
                set_last_sync(cliente, plat, agora)
        else:
            err_count += 1

    print()
    print("=" * 50)
    print(f"  Enviados : {ok_count}")
    print(f"  Sem dados: {skip_count}")
    print(f"  Erros    : {err_count}")
    print("=" * 50)


# ============================================================
# CLI
# ============================================================

def _parse():
    p = argparse.ArgumentParser(description="Envia dados para o dashboard Supabase")
    p.add_argument("--force",    action="store_true", help="Ignora last_sync_at")
    p.add_argument("--dias",     type=int, default=None, help="Lookback em dias")
    p.add_argument("--dry-run",  action="store_true", help="Mostra payload sem enviar")
    p.add_argument("--plat",     nargs="*", help="Filtra plataformas (meta_ads google_ads shopify)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse()
    run(
        force=args.force,
        dias=args.dias,
        dry_run=args.dry_run,
        plataformas=args.plat,
    )
