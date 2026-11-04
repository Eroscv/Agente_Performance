"""
scripts/rebuild_bq_layers.py

Reconstroi as camadas silver (ingestao/) e gold (correlacao/) no BigQuery a
partir do bronze (guara_performance/), chamando as stored procedures
versionadas em bigquery/scripts/{silver,gold}/proc_load_*.sql.

Pensado para rodar no final da cadeia de sync (depois de ads_diario,
shopify_diario e pedidos_diario já atualizados).

A lógica de transformação em si (o que cada tabela faz) vive nos arquivos
.sql, não aqui — este script só dispara as procedures já criadas no
BigQuery (veja bigquery/docs/naming_conventions.md). Para recriar ou
alterar uma procedure, edite o .sql correspondente e rode-o uma vez no
BigQuery (CREATE OR REPLACE PROCEDURE é idempotente).

Uso:
    python3 scripts/rebuild_bq_layers.py
"""
import os
import sys
import logging
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT = os.environ.get("BQ_PROJECT", "guara-db")


def main():
    from google.cloud import bigquery
    bq = bigquery.Client()

    logger.info("Chamando %s.ingestao.load_silver()...", PROJECT)
    job = bq.query(f"CALL `{PROJECT}.ingestao.load_silver`()")
    job.result()
    logger.info("✅ load_silver OK")

    logger.info("Chamando %s.correlacao.load_gold()...", PROJECT)
    job = bq.query(f"CALL `{PROJECT}.correlacao.load_gold`()")
    job.result()
    logger.info("✅ load_gold OK")


if __name__ == "__main__":
    main()
