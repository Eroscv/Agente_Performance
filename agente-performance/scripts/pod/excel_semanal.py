"""
scripts/pod/excel_semanal.py

Gera Excel semanal exclusivo do POD Kombucha.
Chama o core em scripts/gerar_excel_semanal.py com cliente fixo.

Uso:
    python scripts/pod/excel_semanal.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    from scripts.gerar_excel_semanal import gerar_cliente
    logger.info("Gerando Excel semanal — POD Kombucha")
    out = gerar_cliente("pod_kombucha")
    if out:
        logger.info("✅ Salvo: %s", out)
    else:
        logger.warning("⚠️  Nenhum dado disponível para POD Kombucha")


if __name__ == "__main__":
    main()
