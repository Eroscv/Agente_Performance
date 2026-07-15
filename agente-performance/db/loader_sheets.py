"""
db/loader_sheets.py

Baixa dados diretamente da planilha Google Sheets (pública) e carrega no SQLite.
Complementa ou substitui o loader_csv quando a planilha for a fonte de dados.

Uso:
    python -m db.loader_sheets              # baixa e carrega tudo
    python -m db.loader_sheets --append     # só adiciona linhas novas (por data)
    python -m db.loader_sheets --dry-run    # só imprime resumo, não salva

Mapeamento de colunas (inglês → schema interno):
    Campaign name                          → campanha
    Ad Name                                → ad_name
    Day                                    → data_inicio (= data_fim)
    Adset Name                             → adset_name
    Impressions                            → impressoes
    Amount spent                           → investimento
    Cost per purchase                      → cpa_meta
    Link clicks                            → cliques_link
    Purchase ROAS (return on ad spend)     → roas_compras
    Purchases                              → compras
    Purchases conversion value             → valor_compras
    Ad Id                                  → ad_id
    Campaign ID                            → campaign_id
    Objective                              → objetivo
    Creative status                        → status_criativo
    Ad set start time                      → adset_start
    3-second video plays                   → video_3s
    Video plays at 50%                     → video_50pct
    ThruPlays                              → thruplays
"""

import argparse
import csv
import io
import logging
import sqlite3
import urllib.request
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR   = Path(__file__).parent.parent
DB_PATH    = BASE_DIR / "data" / "campanhas.db"

SHEET_ID   = "16-VxSJD8PKexTKQiSoOE1SO0kRSXV3GkXgb50z3kwgw"
GID        = "856747855"
EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"

# Colunas da planilha (inglês) → colunas do banco
COL_MAP = {
    "Campaign name":                        "campanha",
    "Ad Name":                              "ad_name",
    "Day":                                  "data_inicio",
    "Adset Name":                           "adset_name",
    "Impressions":                          "impressoes",
    "Amount spent":                         "investimento",
    "Cost per purchase":                    "cpa_meta",
    "Link clicks":                          "cliques_link",
    "Purchase ROAS (return on ad spend)":   "roas_compras",
    "Purchases":                            "compras",
    "Purchases conversion value":           "valor_compras",
    "Ad Id":                                "ad_id",
    "Campaign ID":                          "campaign_id",
    "Objective":                            "objetivo",
    "Creative status":                      "status_criativo",
    "Ad set start time":                    "adset_start",
    "3-second video plays":                 "video_3s",
    "Video plays at 50%":                   "video_50pct",
    "ThruPlays":                            "thruplays",
}

# Colunas numéricas (float)
FLOAT_COLS = {
    "investimento", "cpa_meta", "cliques_link", "roas_compras",
    "compras", "valor_compras", "impressoes", "video_3s",
    "video_50pct", "thruplays",
}

# DDL da tabela de anúncios (granularidade por anúncio/dia)
CREATE_ADS_TABLE = """
CREATE TABLE IF NOT EXISTS ads_diario (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    campanha         TEXT,
    ad_name          TEXT,
    data_inicio      TEXT,
    adset_name       TEXT,
    impressoes       REAL,
    investimento     REAL,
    cpa_meta         REAL,
    cliques_link     REAL,
    roas_compras     REAL,
    compras          REAL,
    valor_compras    REAL,
    ad_id            TEXT,
    campaign_id      TEXT,
    objetivo         TEXT,
    status_criativo  TEXT,
    adset_start      TEXT,
    video_3s         REAL,
    video_50pct      REAL,
    thruplays        REAL,
    UNIQUE(campanha, ad_name, data_inicio, adset_name)
);
"""

# View agregada por campanha/dia (compatível com banco principal)
CREATE_VIEW_CAMPANHAS = """
CREATE VIEW IF NOT EXISTS v_campanhas_sheets AS
SELECT
    campanha,
    data_inicio,
    data_inicio              AS data_fim,
    SUM(impressoes)          AS impressoes,
    SUM(investimento)        AS investimento,
    SUM(cliques_link)        AS cliques_link,
    SUM(compras)             AS compras,
    SUM(valor_compras)       AS valor_compras,
    SUM(video_3s)            AS video_3s,
    SUM(thruplays)           AS thruplays,
    -- ROAS recalculado (usando regra de negócio: investimento * 1.1215)
    CASE
        WHEN SUM(investimento) * 1.1215 > 0
        THEN SUM(valor_compras) / (SUM(investimento) * 1.1215)
        ELSE NULL
    END AS roas_calculado,
    -- CPA recalculado
    CASE
        WHEN SUM(compras) > 0
        THEN SUM(investimento) * 1.1215 / SUM(compras)
        ELSE NULL
    END AS cpa_calculado,
    -- CTR
    CASE
        WHEN SUM(impressoes) > 0
        THEN SUM(cliques_link) / SUM(impressoes) * 100.0
        ELSE NULL
    END AS ctr_link
FROM ads_diario
GROUP BY campanha, data_inicio;
"""


def _to_float(v: str) -> Optional[float]:
    if not v or not v.strip():
        return None
    try:
        return float(v.replace(",", ".").strip())
    except ValueError:
        return None


def baixar_csv() -> list[dict]:
    """Baixa a planilha e retorna lista de dicts com colunas mapeadas."""
    logger.info("Baixando planilha: %s", EXPORT_URL)
    req = urllib.request.Request(EXPORT_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")

    lines = raw.splitlines()

    # Encontra a linha de header real (começa com "Campaign name")
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Campaign name"):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Header 'Campaign name' não encontrado na planilha")

    content = "\n".join(lines[header_idx:])
    raw_rows = list(csv.DictReader(io.StringIO(content)))

    rows = []
    for r in raw_rows:
        campanha = r.get("Campaign name", "").strip()
        if not campanha or campanha == "Campaign name":
            continue
        dia = r.get("Day", "").strip()
        if not dia:
            continue

        mapped = {}
        for eng_col, db_col in COL_MAP.items():
            val = r.get(eng_col, "").strip()
            if db_col in FLOAT_COLS:
                mapped[db_col] = _to_float(val)
            else:
                mapped[db_col] = val or None

        # data_fim = data_inicio (granularidade diária)
        mapped["data_fim"] = mapped["data_inicio"]
        rows.append(mapped)

    logger.info("%d linhas válidas baixadas (período: %s → %s)",
                len(rows),
                min(r["data_inicio"] for r in rows),
                max(r["data_inicio"] for r in rows))
    return rows


def carregar(rows: list[dict], append: bool = False) -> int:
    """Carrega as linhas no SQLite. Se append=False, limpa a tabela antes."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute(CREATE_ADS_TABLE)

    # Remove view antiga se existir (pode mudar)
    conn.execute("DROP VIEW IF EXISTS v_campanhas_sheets")
    conn.execute(CREATE_VIEW_CAMPANHAS)

    if not append:
        conn.execute("DELETE FROM ads_diario")
        logger.info("Tabela ads_diario limpa (modo replace)")

    cols = [
        "campanha", "ad_name", "data_inicio", "adset_name",
        "impressoes", "investimento", "cpa_meta", "cliques_link",
        "roas_compras", "compras", "valor_compras", "ad_id",
        "campaign_id", "objetivo", "status_criativo", "adset_start",
        "video_3s", "video_50pct", "thruplays",
    ]
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"""
        INSERT OR REPLACE INTO ads_diario ({', '.join(cols)})
        VALUES ({placeholders})
    """

    inserted = 0
    for r in rows:
        values = [r.get(c) for c in cols]
        conn.execute(sql, values)
        inserted += 1

    conn.commit()
    conn.close()
    logger.info("%d linhas carregadas em ads_diario", inserted)
    return inserted


def resumo_banco() -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    r = conn.execute("""
        SELECT COUNT(*) AS n, MIN(data_inicio) AS inicio, MAX(data_inicio) AS fim,
               COUNT(DISTINCT campanha) AS campanhas
        FROM ads_diario
    """).fetchone()
    conn.close()
    return dict(r)


def main():
    parser = argparse.ArgumentParser(description="Loader Google Sheets → SQLite")
    parser.add_argument("--append",  action="store_true", help="Adiciona sem limpar tabela")
    parser.add_argument("--dry-run", action="store_true", help="Só imprime resumo")
    args = parser.parse_args()

    rows = baixar_csv()

    if args.dry_run:
        campanhas = set(r["campanha"] for r in rows)
        print(f"\n{'='*50}")
        print(f"  DRY-RUN — não salvou nada")
        print(f"  Linhas: {len(rows)}")
        print(f"  Período: {min(r['data_inicio'] for r in rows)} → {max(r['data_inicio'] for r in rows)}")
        print(f"  Campanhas ({len(campanhas)}): {sorted(campanhas)}")
        print(f"{'='*50}\n")
        return

    n = carregar(rows, append=args.append)
    info = resumo_banco()

    print(f"\n{'='*50}")
    print(f"  ✅ Sheets → SQLite concluído")
    print(f"  Linhas inseridas:  {n}")
    print(f"  Total no banco:    {info['n']}")
    print(f"  Período:           {info['inicio']} → {info['fim']}")
    print(f"  Campanhas únicas:  {info['campanhas']}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
