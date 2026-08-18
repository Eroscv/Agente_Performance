"""
scripts/source_of/relatorio_diario.py

Script de relatório diário exclusivo da Source OF.
Chama o core em scripts/relatorio_diario.py com cliente fixo.

Uso:
    python scripts/source_of/relatorio_diario.py
    python scripts/source_of/relatorio_diario.py --data 2026-08-17
    python scripts/source_of/relatorio_diario.py --plataforma meta_ads
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argparse
import logging
from datetime import date, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CLIENTE_KEY = "source_of"


def main():
    parser = argparse.ArgumentParser(description="Relatório diário Source OF")
    parser.add_argument("--data",      default=None, help="YYYY-MM-DD (padrão: ontem)")
    parser.add_argument("--plataforma",default=None, help="meta_ads | google_ads (padrão: todas ativas)")
    parser.add_argument("--publicar",  action="store_true", default=True)
    args = parser.parse_args()

    data = args.data or (date.today() - timedelta(days=1)).isoformat()

    from scripts.relatorio_diario import enviar_relatorio_cliente
    from db.registry import get_cliente

    cfg = get_cliente(CLIENTE_KEY)
    plataformas = (
        [args.plataforma] if args.plataforma
        else [p for p, pc in cfg.get("plataformas", {}).items()
              if pc.get("ativo") and p != "shopify"]
    )

    logger.info("Source OF · Relatório diário %s · plataformas=%s", data, plataformas)
    enviados = 0
    for plat in plataformas:
        ok = enviar_relatorio_cliente(data, CLIENTE_KEY, plat)
        if ok:
            enviados += 1
            logger.info("  ✅ %s · %s enviado", CLIENTE_KEY, plat)
        else:
            logger.warning("  ⚠️  %s · %s sem dados ou falhou", CLIENTE_KEY, plat)

    logger.info("Concluído: %d/%d plataformas", enviados, len(plataformas))


if __name__ == "__main__":
    main()
