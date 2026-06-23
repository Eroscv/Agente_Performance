"""
db/loader.py

Carrega o CSV de campanhas no banco SQLite.
- Normaliza cabeçalhos usando COLUMN_MAP
- Converte tipos (datas, números)
- Upsert idempotente: limpa e recarrega

Uso:
    python -m db.loader
    ou importar load_csv() em outro módulo
"""

import csv
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

from db.schema import COLUMN_MAP, CREATE_TABLE_SQL

logger = logging.getLogger(__name__)

BASE_DIR   = Path(__file__).parent.parent
CSV_PATH   = BASE_DIR / "data" / "campanhas.csv"
DB_PATH    = BASE_DIR / "data" / "campanhas.db"


def _to_float(val: str) -> Optional[float]:
    if not val or val.strip() == "":
        return None
    try:
        return float(val.replace(",", "."))
    except ValueError:
        return None


def _to_int(val: str) -> Optional[int]:
    f = _to_float(val)
    return int(f) if f is not None else None


def _to_date(val: str) -> Optional[str]:
    """Aceita YYYY-MM-DD (já vem nesse formato do Meta)."""
    if not val or val.strip() == "":
        return None
    return val.strip()


def load_csv(csv_path: Path = CSV_PATH, db_path: Path = DB_PATH) -> int:
    """
    Carrega o CSV no SQLite.
    Recria a tabela do zero (truncate + insert).
    Retorna número de linhas inseridas.
    """
    logger.info("Iniciando carga: %s → %s", csv_path, db_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()

    # Cria schema
    for stmt in CREATE_TABLE_SQL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            cur.execute(stmt)

    # Limpa dados anteriores
    cur.execute("DELETE FROM campanhas")

    inserted = 0
    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        # Mapeia cabeçalhos originais → nomes SQL
        col_names = [COLUMN_MAP.get(h, None) for h in reader.fieldnames]

        # Colunas de data e texto (não numéricas)
        date_cols = {"data_inicio", "data_fim"}
        text_cols = {"campanha", "indicador_resultado", "indicador_valor_resultado"}

        placeholders = ", ".join(["?"] * len(col_names))
        valid_cols   = [c for c in col_names if c is not None]
        col_list     = ", ".join(valid_cols)

        batch = []
        for row in reader:
            values = []
            for orig_header, sql_col in zip(reader.fieldnames, col_names):
                if sql_col is None:
                    continue
                raw = row.get(orig_header, "")
                if sql_col in date_cols:
                    values.append(_to_date(raw))
                elif sql_col in text_cols:
                    values.append(raw.strip() if raw else None)
                else:
                    values.append(_to_float(raw))

            batch.append(tuple(values))

            if len(batch) >= 500:
                cur.executemany(
                    f"INSERT INTO campanhas ({col_list}) VALUES ({', '.join(['?']*len(valid_cols))})",
                    batch,
                )
                inserted += len(batch)
                batch = []

        if batch:
            cur.executemany(
                f"INSERT INTO campanhas ({col_list}) VALUES ({', '.join(['?']*len(valid_cols))})",
                batch,
            )
            inserted += len(batch)

    conn.commit()
    conn.close()
    logger.info("Carga concluída: %d linhas inseridas", inserted)
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    n = load_csv()
    print(f"✅ {n} linhas carregadas em {DB_PATH}")
