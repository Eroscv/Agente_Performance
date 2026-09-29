"""
scripts/sync_pod_meta_local.py

Sincroniza dados de data/performance/pod_kombucha/meta_ads.db
→ campanhas.db principal (tabela pod_kombucha_meta_ads_diario).

Roda após o sync da Meta API para garantir que o campanhas.db
legado (usado pelo gerador de Excel e pelo agente) fique atualizado.

Uso:
    python -m scripts.sync_pod_meta_local
    python -m scripts.sync_pod_meta_local --dias 7
"""
import argparse
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR   = Path(__file__).parent.parent
DB_SRC     = BASE_DIR / "data" / "performance" / "pod_kombucha" / "meta_ads.db"
DB_DST     = BASE_DIR / "data" / "campanhas.db"
TABELA     = "pod_kombucha_meta_ads_diario"


def sincronizar(dias: int = 8) -> dict:
    """
    Copia os últimos `dias` dias do DB de performance para o campanhas.db.
    Usa INSERT OR REPLACE para upsert seguro.
    """
    if not DB_SRC.exists():
        raise FileNotFoundError(f"DB fonte não encontrado: {DB_SRC}")

    data_corte = (date.today() - timedelta(days=dias)).isoformat()

    src = sqlite3.connect(str(DB_SRC))
    dst = sqlite3.connect(str(DB_DST))
    dst.execute("PRAGMA journal_mode=WAL")

    # Colunas sem 'id' (auto-increment no destino)
    cols = [c[1] for c in src.execute(f"PRAGMA table_info({TABELA})").fetchall() if c[1] != "id"]

    rows = src.execute(
        f"SELECT {','.join(cols)} FROM {TABELA} WHERE data >= ? ORDER BY data",
        (data_corte,)
    ).fetchall()

    if not rows:
        logger.info("[sync_pod_meta_local] Nenhuma linha para copiar (desde %s)", data_corte)
        src.close(); dst.close()
        return {"copiadas": 0, "periodo": data_corte}

    ph  = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO {TABELA} ({','.join(cols)}) VALUES ({ph})"
    dst.executemany(sql, rows)
    dst.commit()

    # Confirmar
    r_dst = dst.execute(
        f"SELECT MIN(data), MAX(data), COUNT(*) FROM {TABELA}"
    ).fetchone()
    logger.info(
        "[sync_pod_meta_local] OK: %d linhas copiadas | campanhas.db agora: %s → %s (%d rows)",
        len(rows), r_dst[0], r_dst[1], r_dst[2]
    )

    src.close(); dst.close()
    return {"copiadas": len(rows), "periodo_inicio": data_corte, "db_max": r_dst[1]}


def sincronizar_shopify() -> dict:
    """
    Copia dados de shopify_receita.db POD → pod_shopify_diario no campanhas.db.
    Cria a tabela se não existir.
    """
    db_shp_src = BASE_DIR / "data" / "performance" / "pod_kombucha" / "shopify_receita.db"
    if not db_shp_src.exists():
        logger.warning("[sync_pod_shopify] DB Shopify POD não encontrado: %s", db_shp_src)
        return {"copiadas": 0}

    src = sqlite3.connect(str(db_shp_src))
    dst = sqlite3.connect(str(DB_DST))
    dst.execute("PRAGMA journal_mode=WAL")

    dst.execute("""
        CREATE TABLE IF NOT EXISTS pod_shopify_diario (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            data              DATE    NOT NULL UNIQUE,
            receita           REAL    NOT NULL DEFAULT 0,
            pedidos           INTEGER NOT NULL DEFAULT 0,
            novos_clientes    INTEGER NOT NULL DEFAULT 0,
            recompradores     INTEGER NOT NULL DEFAULT 0,
            ticket_medio      REAL,
            total_desconto    REAL    NOT NULL DEFAULT 0,
            pedidos_acima_300 INTEGER NOT NULL DEFAULT 0,
            atualizado_em     TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    rows = src.execute("""
        SELECT data, receita, pedidos, novos_clientes, recompradores,
               ticket_medio, total_desconto, pedidos_acima_300
        FROM shopify_receita_diaria ORDER BY data
    """).fetchall()

    dst.executemany("""
        INSERT OR REPLACE INTO pod_shopify_diario
            (data, receita, pedidos, novos_clientes, recompradores,
             ticket_medio, total_desconto, pedidos_acima_300, atualizado_em)
        VALUES (?,?,?,?,?,?,?,?,datetime('now'))
    """, rows)
    dst.commit()

    r = dst.execute("SELECT MIN(data), MAX(data), COUNT(*) FROM pod_shopify_diario").fetchone()
    logger.info("[sync_pod_shopify] OK: %d rows | %s → %s", r[2], r[0], r[1])

    src.close(); dst.close()
    return {"copiadas": len(rows), "db_max": r[1]}


def main():
    parser = argparse.ArgumentParser(description="Sync POD meta_ads.db → campanhas.db")
    parser.add_argument("--dias", type=int, default=8, help="Dias a sincronizar (padrão 8)")
    args = parser.parse_args()

    r1 = sincronizar(dias=args.dias)
    r2 = sincronizar_shopify()
    print(f"Meta: {r1['copiadas']} linhas | Shopify: {r2['copiadas']} linhas")


if __name__ == "__main__":
    main()
