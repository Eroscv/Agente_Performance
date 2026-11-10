"""
scripts/apply_bq_sql.py

Único caminho para aplicar SQL versionado (views, procedures, funções, DDL) no BigQuery de
produção. Para cada arquivo:
  - recusa se o arquivo tem alteração não commitada (só vai para produção o que está no git);
  - recusa se a branch atual é a de trabalho sem commit do arquivo;
  - executa e grava em correlacao.log_alteracoes_bq quem aplicou, quando, o commit e o hash.

Revisão: a mudança entra por branch e pull request com revisor (veja
bigquery/docs/revisao_de_mudancas.md). Este script não substitui a revisão, só garante que o
que roda em produção é o que foi commitado e que fica registro.

Uso:
    python3 scripts/apply_bq_sql.py --revisor "Miguel" bigquery/scripts/views/guara_performance/v_dash_overview.sql
    python3 scripts/apply_bq_sql.py --revisor "Miguel" arquivo1.sql arquivo2.sql
    python3 scripts/apply_bq_sql.py --forcar arquivo.sql     # sem checar o git (fica registrado)
"""
import argparse
import hashlib
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT = os.environ.get("BQ_PROJECT", "guara-db")
LOG_TABLE = f"{PROJECT}.correlacao.log_alteracoes_bq"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True, text=True, check=True).stdout.strip()


def _arquivo_commitado(caminho: Path) -> bool:
    return _git("status", "--porcelain", "--", str(caminho)) == ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("arquivos", nargs="+")
    ap.add_argument("--revisor", default=None, help="Quem revisou a mudança (pull request)")
    ap.add_argument("--forcar", action="store_true", help="Não exige arquivo commitado (fica registrado)")
    args = ap.parse_args()

    if not args.revisor and not args.forcar:
        logger.error('Informe quem revisou: --revisor "Miguel" (ou --forcar, que fica registrado como forçado)')
        return 1

    caminhos = [Path(a).resolve() for a in args.arquivos]
    if not args.forcar:
        sujos = [str(c) for c in caminhos if not _arquivo_commitado(c)]
        if sujos:
            logger.error("Arquivo(s) com alteração não commitada, nada foi aplicado:\n  %s", "\n  ".join(sujos))
            return 1

    from google.cloud import bigquery
    bq = bigquery.Client(project=PROJECT)
    bq.query(f"""CREATE TABLE IF NOT EXISTS `{LOG_TABLE}` (
        aplicado_em TIMESTAMP, aplicado_por STRING, arquivo STRING, sha256 STRING,
        commit_git STRING, branch STRING, revisor STRING, forcado BOOL
    ) PARTITION BY DATE(aplicado_em)""").result()

    commit = _git("rev-parse", "--short", "HEAD")
    branch = _git("branch", "--show-current")
    quem = _git("config", "user.name") or os.environ.get("USER", "desconhecido")

    for c in caminhos:
        sql = c.read_text()
        bq.query(sql).result()
        erros = bq.insert_rows_json(LOG_TABLE, [{
            "aplicado_em": datetime.now(timezone.utc).isoformat(), "aplicado_por": quem,
            "arquivo": str(c.relative_to(BASE_DIR)), "sha256": hashlib.sha256(sql.encode()).hexdigest(),
            "commit_git": commit, "branch": branch, "revisor": args.revisor, "forcado": args.forcar}])
        if erros:
            logger.warning("aplicado, mas o registro falhou: %s", erros)
        logger.info("✅ %s aplicado (commit %s, revisor %s)", c.name, commit, args.revisor or "não informado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
