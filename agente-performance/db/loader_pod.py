"""
db/loader_pod.py

Baixa dados do Google Ads (POD Kombucha) via Google Sheets e carrega no SQLite.

⚠️  DADOS EXCLUSIVOS DO CLIENTE POD KOMBUCHA — nunca misturar com Source Of.

Planilha: https://docs.google.com/spreadsheets/d/1Vj3Zt1ZPk5nH_bBbdm1gna0cMC85PGN-pBtCKr_UJ50

Colunas da planilha:
    Day             → data_inicio (= data_fim)
    Clicks          → cliques
    Cost            → investimento  (formato "$1,234.56" → float BRL aproximado)
    Impr.           → impressoes
    Conv. value     → valor_conversao
    Conversions     → conversoes
    Campaign        → campanha
    Campaign ID     → campaign_id
    Campaign type   → tipo_campanha

Tabela SQLite: pod_gads_diario
"""

import argparse
import csv
import io
import logging
import sqlite3
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"

SHEETS_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1Vj3Zt1ZPk5nH_bBbdm1gna0cMC85PGN-pBtCKr_UJ50"
    "/export?format=csv"
)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pod_gads_diario (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    data_inicio   TEXT NOT NULL,
    data_fim      TEXT NOT NULL,
    campanha      TEXT,
    campaign_id   TEXT,
    tipo_campanha TEXT,
    impressoes    REAL,
    cliques       REAL,
    investimento  REAL,
    conversoes    REAL,
    valor_conversao REAL,
    UNIQUE(data_inicio, campanha, campaign_id)
)
"""


def _parse_date(raw: str) -> Optional[str]:
    """Converte 'DD/MM/YYYY' → 'YYYY-MM-DD'. Retorna None em caso de erro."""
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_float(raw: str) -> Optional[float]:
    """Remove $ e espaços; trata separador decimal como vírgula (formato brasileiro do Google Ads)."""
    if not raw or raw.strip() in ("", "--", "-"):
        return None
    cleaned = raw.strip().replace("$", "").replace(" ", "")
    # Formato brasileiro: vírgula = decimal, ponto = milhar (ex: '1.234,56')
    # Formato americano com $: ponto = decimal, vírgula = milhar (ex: '$1,234.56')
    # No Google Ads exportado em PT-BR: '$40,00' = 40 (vírgula é decimal)
    if ',' in cleaned and '.' not in cleaned:
        # Só vírgula: substitui por ponto
        cleaned = cleaned.replace(',', '.')
    elif '.' in cleaned and ',' in cleaned:
        # Ambos: ponto = milhar, vírgula = decimal (ex: '1.234,56')
        cleaned = cleaned.replace('.', '').replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return None


def baixar_csv() -> list[dict]:
    """Baixa planilha e retorna lista de dicts com dados normalizados."""
    import requests
    logger.info("Baixando planilha POD Kombucha: %s", SHEETS_URL)
    r = requests.get(SHEETS_URL, timeout=30)
    r.raise_for_status()

    reader = csv.reader(io.StringIO(r.text))
    all_rows = list(reader)

    # Linha 0: cabeçalho descritivo ("Gads POD...") — ignorar
    # Linha 1: header real — Day, Clicks, Cost, Impr., Conv. value, Conversions, Campaign, Campaign ID, Campaign type
    # Linha 2+: dados
    if len(all_rows) < 3:
        logger.warning("Planilha vazia ou sem dados")
        return []

    header = [h.strip() for h in all_rows[1]]
    col = {h: i for i, h in enumerate(header)}

    resultado = []
    skipped = 0
    for row in all_rows[2:]:
        if not row or not row[0].strip() or row[0].strip() in ("Total", "total", ""):
            skipped += 1
            continue

        data = _parse_date(row[col.get("Day", 0)])
        if not data:
            skipped += 1
            continue

        resultado.append({
            "data_inicio":    data,
            "data_fim":       data,
            "campanha":       row[col.get("Campaign", 6)].strip() if col.get("Campaign", 6) < len(row) else None,
            "campaign_id":    row[col.get("Campaign ID", 7)].strip() if col.get("Campaign ID", 7) < len(row) else None,
            "tipo_campanha":  row[col.get("Campaign type", 8)].strip() if col.get("Campaign type", 8) < len(row) else None,
            "impressoes":     _parse_float(row[col.get("Impr.", 3)] if col.get("Impr.", 3) < len(row) else ""),
            "cliques":        _parse_float(row[col.get("Clicks", 1)] if col.get("Clicks", 1) < len(row) else ""),
            "investimento":   _parse_float(row[col.get("Cost", 2)] if col.get("Cost", 2) < len(row) else ""),
            "conversoes":     _parse_float(row[col.get("Conversions", 5)] if col.get("Conversions", 5) < len(row) else ""),
            "valor_conversao":_parse_float(row[col.get("Conv. value", 4)] if col.get("Conv. value", 4) < len(row) else ""),
        })

    datas_validas = [r["data_inicio"] for r in resultado]
    periodo = f"{min(datas_validas)} → {max(datas_validas)}" if datas_validas else "—"
    logger.info("%d linhas válidas baixadas (período: %s) | %d ignoradas", len(resultado), periodo, skipped)
    return resultado


def carregar(rows: list[dict], append: bool = False) -> int:
    """Carrega os dados no SQLite. Se append=False, limpa a tabela antes."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(CREATE_TABLE)

    if not append:
        conn.execute("DELETE FROM pod_gads_diario")
        logger.info("Tabela pod_gads_diario limpa (modo replace)")

    cols = [
        "data_inicio", "data_fim", "campanha", "campaign_id", "tipo_campanha",
        "impressoes", "cliques", "investimento", "conversoes", "valor_conversao",
    ]
    sql = f"""
        INSERT OR REPLACE INTO pod_gads_diario ({', '.join(cols)})
        VALUES ({', '.join(['?'] * len(cols))})
    """
    inserted = 0
    for r in rows:
        conn.execute(sql, [r.get(c) for c in cols])
        inserted += 1

    conn.commit()
    conn.close()
    logger.info("%d linhas carregadas em pod_gads_diario", inserted)
    return inserted


def resumo_banco() -> dict:
    """Retorna estatísticas do banco POD."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    r = conn.execute("""
        SELECT COUNT(*) AS n,
               MIN(data_inicio) AS inicio,
               MAX(data_inicio) AS fim,
               COUNT(DISTINCT campanha) AS campanhas
        FROM pod_gads_diario
    """).fetchone()
    conn.close()
    return dict(r)


def carregar_pod(append: bool = False):
    """
    Wrapper programático — baixa planilha e carrega no SQLite.
    Chamado automaticamente pelo builder antes de gerar relatórios POD.

    Returns:
        (linhas_inseridas: int, periodo: str)
    """
    rows   = baixar_csv()
    linhas = carregar(rows, append=append)
    info   = resumo_banco()
    periodo = f"{info.get('inicio', '?')} → {info.get('fim', '?')}"
    logger.info("[POD] %d linhas | período: %s", linhas, periodo)
    return linhas, periodo


def main():
    parser = argparse.ArgumentParser(description="Loader Google Ads POD Kombucha → SQLite")
    parser.add_argument("--append",  action="store_true", help="Adiciona sem limpar tabela")
    parser.add_argument("--dry-run", action="store_true", help="Só imprime resumo, não salva")
    args = parser.parse_args()

    rows = baixar_csv()

    if args.dry_run:
        datas = [r["data_inicio"] for r in rows]
        print(f"\n{'='*50}")
        print(f"  DRY RUN — POD Kombucha Google Ads")
        print(f"  Linhas: {len(rows)}")
        print(f"  Período: {min(datas)} → {max(datas)}" if datas else "  Sem dados")
        print(f"{'='*50}\n")
        return

    inserted = carregar(rows, append=args.append)
    info = resumo_banco()

    print(f"\n{'='*50}")
    print(f"  ✅ Sheets → SQLite (POD Kombucha)")
    print(f"  Linhas inseridas:  {inserted}")
    print(f"  Total no banco:    {info['n']}")
    print(f"  Período:           {info['inicio']} → {info['fim']}")
    print(f"  Campanhas únicas:  {info['campanhas']}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
