"""
scripts/bq_alter_compras_float.py

Altera a coluna `compras` da tabela ads_diario de INT64 → FLOAT64 no BigQuery.

BigQuery não suporta ALTER COLUMN TYPE diretamente.
Estratégia:
  1. Cria ads_diario_new com schema correto (compras FLOAT64)
  2. Copia todos os dados: CAST(compras AS FLOAT64)
  3. Renomeia: ads_diario → ads_diario_bak | ads_diario_new → ads_diario
  4. Apaga backup (ou mantém por segurança)

Uso:
    cd /home/ubuntu/.openclaw/workspace/agente-performance
    python scripts/bq_alter_compras_float.py [--dry-run] [--keep-bak]
"""
import argparse
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",  action="store_true", help="Mostra DDL mas não executa")
    parser.add_argument("--keep-bak", action="store_true", help="Mantém ads_diario_bak após migração")
    args = parser.parse_args()

    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not creds:
        creds = str(BASE_DIR / "credentials" / "bq-service-account.json")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds

    from google.cloud import bigquery
    bq = bigquery.Client()
    project = os.environ.get("BQ_PROJECT", bq.project)
    dataset = os.environ.get("BQ_DATASET", "guara_performance")

    src   = f"`{project}.{dataset}.ads_diario`"
    new_  = f"`{project}.{dataset}.ads_diario_new`"
    bak_  = f"`{project}.{dataset}.ads_diario_bak`"

    # 1. Lê schema atual
    table = bq.get_table(f"{project}.{dataset}.ads_diario")
    current_types = {f.name: f.field_type for f in table.schema}
    compras_type = current_types.get("compras", "DESCONHECIDO")

    logger.info("Tipo atual de 'compras': %s", compras_type)
    if compras_type == "FLOAT":
        logger.info("✅ Coluna 'compras' já é FLOAT64 — nenhuma ação necessária.")
        return

    # 2. Monta schema novo com compras FLOAT64
    new_schema = []
    for f in table.schema:
        if f.name == "compras":
            new_schema.append(bigquery.SchemaField("compras", "FLOAT64", mode=f.mode,
                                                    description="Conversões (FLOAT64 — Google Ads retorna frações)"))
        else:
            new_schema.append(f)

    # 3. Monta DDL do CTAS
    col_exprs = []
    for f in table.schema:
        if f.name == "compras":
            col_exprs.append("CAST(compras AS FLOAT64) AS compras")
        else:
            col_exprs.append(f.name)
    select_cols = ",\n  ".join(col_exprs)

    ctas_sql = f"""
CREATE OR REPLACE TABLE {new_}
AS
SELECT
  {select_cols}
FROM {src}
"""

    rename_to_bak = f"ALTER TABLE {src} RENAME TO `ads_diario_bak`"
    rename_new    = f"ALTER TABLE {new_} RENAME TO `ads_diario`"
    drop_bak      = f"DROP TABLE IF EXISTS {bak_}"

    if args.dry_run:
        print("\n=== DRY RUN — DDL que seria executado ===\n")
        print("-- STEP 1: criar ads_diario_new com compras FLOAT64")
        print(ctas_sql)
        print("-- STEP 2: renomear ads_diario → ads_diario_bak")
        print(rename_to_bak)
        print("-- STEP 3: renomear ads_diario_new → ads_diario")
        print(rename_new)
        if not args.keep_bak:
            print("-- STEP 4: dropar backup")
            print(drop_bak)
        print("\n=== Schema novo (compras) ===")
        print("  compras: FLOAT64")
        return

    # 4. Executa
    logger.info("STEP 1 — criando ads_diario_new com CAST(compras AS FLOAT64)...")
    bq.query(ctas_sql).result()
    logger.info("✅ ads_diario_new criada")

    logger.info("STEP 2 — renomeando ads_diario → ads_diario_bak...")
    bq.query(rename_to_bak).result()
    logger.info("✅ ads_diario → ads_diario_bak")

    logger.info("STEP 3 — renomeando ads_diario_new → ads_diario...")
    bq.query(rename_new).result()
    logger.info("✅ ads_diario_new → ads_diario")

    if not args.keep_bak:
        logger.info("STEP 4 — dropando backup ads_diario_bak...")
        bq.query(drop_bak).result()
        logger.info("✅ backup removido")
    else:
        logger.info("ℹ️  Backup mantido como ads_diario_bak (--keep-bak)")

    # 5. Confirma schema final
    table_new = bq.get_table(f"{project}.{dataset}.ads_diario")
    for f in table_new.schema:
        if f.name == "compras":
            logger.info("✅ Schema final: compras = %s", f.field_type)
            break

    logger.info("🎉 Migração concluída com sucesso.")


if __name__ == "__main__":
    main()
