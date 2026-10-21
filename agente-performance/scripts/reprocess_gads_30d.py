"""
scripts/reprocess_gads_30d.py

Reprocessa os últimos 30 dias do Google Ads no SQLite + BQ
para POD Kombucha e Source OF — corrigindo compras arredondadas.

Uso:
    cd /home/ubuntu/.openclaw/workspace/agente-performance
    python scripts/reprocess_gads_30d.py [--dias 30] [--cliente pod_kombucha|source_of]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from scripts.sync_gads_diario import sync_cliente, carregar_clientes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias",    type=int, default=30)
    parser.add_argument("--cliente", type=str, default=None,
                        help="Filtrar cliente específico (default: todos com google_ads ativo)")
    args = parser.parse_args()

    clientes = carregar_clientes()
    ok = err = skip = 0

    for key, cfg in clientes.items():
        if args.cliente and key != args.cliente:
            continue
        if not cfg.get("ativo", False):
            skip += 1
            continue

        plat = cfg.get("plataformas", {}).get("google_ads", {})
        if not plat.get("ativo"):
            logger.info("[%s] google_ads inativo — pulando", key)
            skip += 1
            continue

        logger.info("=== Reprocessando %s (%d dias) ===", key, args.dias)
        result = sync_cliente(key, cfg, dias=args.dias)
        if result is True:
            ok += 1
        elif result is None:
            skip += 1
        else:
            err += 1

    logger.info("Reprocessamento concluído: %d OK | %d erros | %d pulados", ok, err, skip)
    sys.exit(1 if err > 0 and ok == 0 else 0)


if __name__ == "__main__":
    main()
