"""
scripts/run_quality_checks.py

Roda as verificações de bigquery/tests/quality_checks_{silver,gold}.sql e grava o
resultado em correlacao.qualidade_dados_checks (uma linha por verificação).
Cada SELECT dos arquivos deve voltar ZERO linhas; qualquer linha é falha.
Falha também abre (ou atualiza) um alerta em correlacao.alertas_qualidade_dados,
e o script sai com código 1 para o cron registrar.

É o último passo de scripts/rebuild_bq_layers.py (depois de load_silver e load_gold).

Uso:
    python3 scripts/run_quality_checks.py
"""
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT = os.environ.get("BQ_PROJECT", "guara-db")
TESTS = BASE_DIR / "bigquery" / "tests"
CAMADAS = {"silver": "ingestao", "gold": "correlacao"}
TABELA_CHECKS = f"`{PROJECT}.correlacao.qualidade_dados_checks`"
TABELA_ALERTAS = f"`{PROJECT}.correlacao.alertas_qualidade_dados`"


def _consultas(arquivo: Path) -> list[str]:
    sem_comentarios = re.sub(r"--[^\n]*", "", arquivo.read_text())
    return [q.strip() for q in sem_comentarios.split(";") if q.strip()]


def main() -> int:
    from google.cloud import bigquery
    bq = bigquery.Client(project=PROJECT)

    checks, falhas = [], []
    for camada, dataset in CAMADAS.items():
        for n, consulta in enumerate(_consultas(TESTS / f"quality_checks_{camada}.sql"), start=1):
            linhas_falha = len(list(bq.query(consulta).result()))
            eixo = f"{camada}_check_{n:02d}"
            checks.append({
                "checagem_id": str(uuid.uuid4()), "tabela": dataset, "eixo": eixo,
                "linhas_falha": linhas_falha, "limite_tolerado": 0.0, "passou": linhas_falha == 0,
            })
            if linhas_falha:
                falhas.append((dataset, eixo, linhas_falha))
                logger.error("❌ %s: %d linha(s) em falha", eixo, linhas_falha)

    agora = datetime.now(timezone.utc).isoformat()
    erros = bq.insert_rows_json(
        f"{PROJECT}.correlacao.qualidade_dados_checks",
        [{**c, "executado_em": agora} for c in checks])
    if erros:
        raise RuntimeError(f"falha ao gravar qualidade_dados_checks: {erros}")

    for dataset, eixo, linhas in falhas:
        descricao = f"{eixo}: {linhas} linha(s) em falha (bigquery/tests/quality_checks_*.sql)"
        existe = list(bq.query(
            f"SELECT 1 FROM {TABELA_ALERTAS} WHERE tabela = '{dataset}' AND eixo = '{eixo}' AND status = 'aberto'"
        ).result())
        if existe:
            bq.query(f"UPDATE {TABELA_ALERTAS} SET descricao = '{descricao}', atualizado_em = CURRENT_TIMESTAMP() "
                     f"WHERE tabela = '{dataset}' AND eixo = '{eixo}' AND status = 'aberto'").result()
        else:
            bq.query(f"INSERT INTO {TABELA_ALERTAS} (alerta_id, tabela, eixo, descricao, status, aberto_em, atualizado_em) "
                     f"VALUES ('{uuid.uuid4()}', '{dataset}', '{eixo}', '{descricao}', 'aberto', "
                     "CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())").result()

    logger.info("%d verificações, %d com falha", len(checks), len(falhas))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
