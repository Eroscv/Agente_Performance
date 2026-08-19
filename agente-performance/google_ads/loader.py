"""
google_ads/loader.py

Puxa dados de campanhas Google Ads via API e carrega no SQLite.
Substitui loader_pod.py (via Sheets) para todos os clientes Google Ads.

Uso programático:
    from google_ads.loader import carregar_gads
    linhas, periodo = carregar_gads(customer_id="4311260590", dias=30)

Uso CLI:
    python -m google_ads.loader --customer-id 4311260590 --dias 30
"""

import argparse
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from google.ads.googleads.errors import GoogleAdsException

from .client import get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS gads_campanhas_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     TEXT NOT NULL,
    data            TEXT NOT NULL,
    campanha        TEXT,
    campaign_id     TEXT,
    tipo_campanha   TEXT,
    status          TEXT,
    impressoes      REAL,
    cliques         REAL,
    investimento    REAL,
    conversoes      REAL,
    valor_conversao REAL,
    ctr             REAL,
    cpc_medio       REAL,
    UNIQUE(customer_id, data, campaign_id)
)
"""

# GAQL — Google Ads Query Language
QUERY = """
    SELECT
        campaign.id,
        campaign.name,
        campaign.advertising_channel_type,
        campaign.status,
        segments.date,
        metrics.impressions,
        metrics.clicks,
        metrics.cost_micros,
        metrics.conversions,
        metrics.conversions_value,
        metrics.ctr,
        metrics.average_cpc,
        metrics.cost_per_conversion,
        metrics.search_impression_share,
        campaign_budget.amount_micros
    FROM campaign
    WHERE segments.date BETWEEN '{date_start}' AND '{date_end}'
      AND campaign.status != 'REMOVED'
    ORDER BY segments.date DESC, metrics.cost_micros DESC
"""


def _micros_to_brl(micros: int) -> float:
    """Converte micros Google Ads → BRL (divide por 1.000.000)."""
    return round(micros / 1_000_000, 2) if micros else 0.0


def buscar_campanhas(customer_id: str, dias: int = 30) -> list[dict]:
    """Busca dados de campanhas via Google Ads API."""
    client   = get_client()
    service  = client.get_service("GoogleAdsService")

    date_end   = date.today() - timedelta(days=1)  # ontem
    date_start = date_end - timedelta(days=dias - 1)

    query = QUERY.format(
        date_start=date_start.strftime("%Y-%m-%d"),
        date_end=date_end.strftime("%Y-%m-%d"),
    )

    logger.info("[%s] Buscando %d dias (%s → %s)", customer_id, dias,
                date_start, date_end)

    try:
        stream = service.search_stream(customer_id=customer_id, query=query)
        rows = []
        for batch in stream:
            for row in batch.results:
                m = row.metrics
                c = row.campaign
                rows.append({
                    "customer_id":         customer_id,
                    "data":                row.segments.date,
                    "campaign_id":         str(c.id),
                    "campanha":            c.name,
                    "tipo_campanha":       c.advertising_channel_type.name,
                    "status":              c.status.name,
                    "impressoes":          float(m.impressions),
                    "cliques":             float(m.clicks),
                    "investimento":        _micros_to_brl(m.cost_micros),
                    "conversoes":          round(m.conversions, 4),
                    "valor_conversao":     round(m.conversions_value, 2),
                    "ctr":                 round(m.ctr * 100, 4),  # %
                    "cpc_medio":           _micros_to_brl(m.average_cpc),
                    # Novas métricas (Melhoria 2)
                    "custo_por_conversao": _micros_to_brl(m.cost_per_conversion) if m.cost_per_conversion else None,
                    "impressao_share":     round(m.search_impression_share, 4) if m.search_impression_share else None,
                    "budget_diario":       _micros_to_brl(row.campaign_budget.amount_micros) if row.campaign_budget.amount_micros else None,
                })

        logger.info("[%s] %d linhas retornadas", customer_id, len(rows))
        return rows

    except GoogleAdsException as ex:
        for error in ex.failure.errors:
            logger.error("[%s] API error: %s", customer_id, error.message)
        raise


# DDL para tabelas nomeadas por cliente
_CREATE_TABLE_TPL = """
CREATE TABLE IF NOT EXISTS {tabela} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data            TEXT NOT NULL,
    campanha        TEXT,
    campaign_id     TEXT,
    tipo_campanha   TEXT,
    status          TEXT,
    impressoes      REAL DEFAULT 0,
    cliques         REAL DEFAULT 0,
    investimento    REAL DEFAULT 0,
    conversoes      REAL DEFAULT 0,
    valor_conversao REAL DEFAULT 0,
    ctr             REAL,
    cpc_medio       REAL,
    custo_por_conversao REAL,
    impressao_share     REAL,
    budget_diario       REAL,
    UNIQUE(data, campaign_id)
)
"""


def salvar(rows: list[dict], append: bool = False,
           tabela: str = "gads_campanhas_diario",
           db_path: str = None) -> int:
    """Salva dados no SQLite na tabela especificada.
    
    Args:
        db_path: caminho do banco destino. None = usa DB_PATH (legado).
    """
    _dest = Path(db_path) if db_path else DB_PATH
    _dest.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_dest))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_TABLE_TPL.format(tabela=tabela))
    # Índices na tabela destino
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabela}_data ON {tabela}(data)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabela}_camp ON {tabela}(campanha)")

    if not append and rows:
        conn.execute(f"DELETE FROM {tabela}")
        logger.info("[%s] Tabela %s limpa (modo replace)", rows[0].get('customer_id','?'), tabela)

    cols = [
        "data", "campaign_id", "campanha", "tipo_campanha",
        "status", "impressoes", "cliques", "investimento",
        "conversoes", "valor_conversao", "ctr", "cpc_medio",
        "custo_por_conversao", "impressao_share", "budget_diario",
    ]
    sql = f"""
        INSERT OR REPLACE INTO {tabela} ({', '.join(cols)})
        VALUES ({', '.join(['?'] * len(cols))})
    """
    for r in rows:
        conn.execute(sql, [r.get(c) for c in cols])

    conn.commit()
    conn.close()
    logger.info("%d linhas salvas em %s", len(rows), tabela)
    return len(rows)


def resumo_banco(tabela: str = "gads_campanhas_diario", db_path: str = None) -> dict:
    """Estatísticas do banco para uma tabela."""
    _dest = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(_dest))
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(f"""
            SELECT COUNT(*) AS n,
                   MIN(data) AS inicio,
                   MAX(data) AS fim,
                   COUNT(DISTINCT campaign_id) AS campanhas,
                   ROUND(SUM(investimento), 2) AS investimento_total
            FROM {tabela}
        """).fetchone()
        return dict(r)
    except Exception:
        return {}
    finally:
        conn.close()


def carregar_gads(customer_id: str, dias: int = 30, append: bool = False,
                  tabela: str = "gads_campanhas_diario",
                  db_path: str = None):
    """
    Wrapper programático — busca via API e salva no SQLite.

    Args:
        customer_id: ID da conta Google Ads (sem hífens)
        dias: quantos dias puxar (padrão 30)
        append: se True, não limpa antes de inserir
        tabela: nome da tabela destino (padrão: gads_campanhas_diario)
        db_path: caminho do banco destino. None = usa DB_PATH (legado).

    Returns:
        (linhas_inseridas: int, periodo: str)
    """
    rows   = buscar_campanhas(customer_id=customer_id, dias=dias)
    linhas = salvar(rows, append=append, tabela=tabela, db_path=db_path)
    info   = resumo_banco(tabela, db_path=db_path)
    periodo = f"{info.get('inicio', '?')} → {info.get('fim', '?')}"
    return linhas, periodo


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google Ads API → SQLite")
    parser.add_argument("--customer-id", required=True, help="ID da conta Google Ads")
    parser.add_argument("--dias", type=int, default=30, help="Dias a buscar")
    parser.add_argument("--append", action="store_true", help="Não limpa antes de inserir")
    parser.add_argument("--dry-run", action="store_true", help="Só mostra o que buscaria")
    args = parser.parse_args()

    cid = args.customer_id.replace("-", "")

    if args.dry_run:
        print(f"\nDRY RUN — customer_id={cid}, dias={args.dias}")
        rows = buscar_campanhas(customer_id=cid, dias=args.dias)
        datas = sorted({r["data"] for r in rows})
        print(f"Linhas: {len(rows)}")
        print(f"Período: {datas[0]} → {datas[-1]}" if datas else "Sem dados")
        import sys; sys.exit(0)

    linhas, periodo = carregar_gads(customer_id=cid, dias=args.dias, append=args.append)
    info = resumo_banco(cid)
    print(f"\n{'='*50}")
    print(f"  ✅ Google Ads API → SQLite")
    print(f"  Customer ID:       {cid}")
    print(f"  Linhas inseridas:  {linhas}")
    print(f"  Total no banco:    {info['n']}")
    print(f"  Período:           {periodo}")
    print(f"  Campanhas únicas:  {info['campanhas']}")
    print(f"  Investimento:      R$ {info['investimento_total']}")
    print(f"{'='*50}\n")
