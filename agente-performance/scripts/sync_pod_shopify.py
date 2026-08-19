"""
scripts/sync_pod_shopify.py

Sync Shopify POD Kombucha → SQLite.
Uso:
    python scripts/sync_pod_shopify.py           # últimos 8 dias
    python scripts/sync_pod_shopify.py --dias 30
    python scripts/sync_pod_shopify.py --full    # desde jan/2024
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sync_pod_shopify")


def main(dias: int = 8, full: bool = False) -> None:
    from shopify.sync_pod import sincronizar, status_sync
    import os

    if not os.getenv("SHOPIFY_POD_TOKEN"):
        logger.error("SHOPIFY_POD_TOKEN não configurado — sync abortado.")
        sys.exit(1)

    if full:
        d_inicio = date(2024, 1, 1)
        d_fim    = date.today()
    else:
        status = status_sync()
        if not status.get("sincronizado"):
            logger.info("Tabela vazia — carga inicial (últimos 365 dias)...")
            dias = 365
        d_fim    = date.today()
        d_inicio = d_fim - timedelta(days=dias - 1)

    logger.info("Sincronizando POD Shopify: %s → %s", d_inicio, d_fim)
    try:
        r = sincronizar(d_inicio, d_fim)
        logger.info(
            "✅ %d dias | R$ %.2f | %d pedidos",
            r["dias_sincronizados"], r["receita_total"], r["pedidos_total"],
        )
    except Exception as e:
        logger.error("❌ Sync falhou: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Shopify POD Kombucha")
    parser.add_argument("--dias", type=int, default=8)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    main(dias=args.dias, full=args.full)
