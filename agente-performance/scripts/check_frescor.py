#!/usr/bin/env python3
"""
scripts/check_frescor.py

Alerta de frescor do BigQuery. Roda FORA da máquina do cron (GitHub Actions,
.github/workflows/alerta-frescor-bq.yml): se o notebook dormir ou o cron parar,
este check ainda dispara.

Verificações:
  1. Camada gold (correlacao.fato_performance_diaria) reconstruída há mais de
     --max-horas-gold horas. O cron_master_sync roda de 3 em 3h e termina com
     load_gold, então a data de modificação da tabela é a hora da última carga.
  2. (--incluir-clientes) Por cliente, último dia com investimento mais velho
     que --max-dias-cliente dias: pega token Meta/Google quebrado.

Saída: imprime o resumo; se SLACK_WEBHOOK_URL estiver definida, também posta no
Slack. Sai com código 1 quando há problema (o job do Actions falha e o GitHub
avisa por e-mail mesmo sem o webhook).

Só lê metadados e uma agregação pequena; não altera nada no BigQuery.

Uso:
    python3 scripts/check_frescor.py
    python3 scripts/check_frescor.py --incluir-clientes
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from google.cloud import bigquery

BRT = ZoneInfo("America/Sao_Paulo")
PROJETO = os.environ.get("BQ_PROJECT", "guara-db")
TABELA_GOLD = f"{PROJETO}.correlacao.fato_performance_diaria"


def checar_gold(client: bigquery.Client, max_horas: float) -> list[str]:
    modificado = client.get_table(TABELA_GOLD).modified
    idade = datetime.now(timezone.utc) - modificado
    if idade <= timedelta(hours=max_horas):
        return []
    quando = modificado.astimezone(BRT).strftime("%d/%m %H:%M")
    return [
        f"Carga do BigQuery parada: a última reconstrução da camada gold foi em "
        f"{quando} (há {idade.total_seconds() / 3600:.1f}h; esperado a cada 3h)."
    ]


def checar_clientes(client: bigquery.Client, max_dias: int) -> list[str]:
    sql = f"""
        SELECT cliente_id, MAX(IF(investimento > 0, data, NULL)) AS ult_invest
        FROM `{TABELA_GOLD}`
        GROUP BY cliente_id
    """
    hoje = datetime.now(BRT).date()
    problemas = []
    for r in client.query(sql).result():
        if r.ult_invest is None:
            problemas.append(f"{r.cliente_id}: nenhum dia com investimento no gold.")
            continue
        atraso = (hoje - r.ult_invest).days
        if atraso > max_dias:
            problemas.append(
                f"{r.cliente_id}: último dia com investimento é "
                f"{r.ult_invest:%d/%m} ({atraso} dias atrás)."
            )
    return problemas


def avisar_slack(texto: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        print("(SLACK_WEBHOOK_URL não definida: só o log e a falha do job avisam)")
        return
    resp = requests.post(url, json={"text": texto}, timeout=15)
    resp.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--max-horas-gold", type=float, default=4.0)
    ap.add_argument("--max-dias-cliente", type=int, default=2)
    ap.add_argument("--incluir-clientes", action="store_true")
    args = ap.parse_args()

    client = bigquery.Client(project=PROJETO)
    problemas = checar_gold(client, args.max_horas_gold)
    if args.incluir_clientes:
        problemas += checar_clientes(client, args.max_dias_cliente)

    if not problemas:
        print("OK: BigQuery dentro do prazo.")
        return 0

    texto = ":warning: *Alerta de dados (BigQuery guara-db)*\n" + "\n".join(
        f"• {p}" for p in problemas
    )
    print(texto)
    avisar_slack(texto)
    return 1


if __name__ == "__main__":
    sys.exit(main())
