"""
scripts/sync_gads_diario.py

Sync diário automático — Google Ads API → SQLite.

Executa para todos os clientes ativos com plataforma=google_ads
e gads_customer_id definido em clientes.json.

Comportamento:
  - Puxa os últimos 8 dias (append=True) para cobrir dados atrasados
  - Registra execução em cron_runs
  - Loga resultado no stdout (capturado pelo cron OpenClaw)

Uso manual:
    python scripts/sync_gads_diario.py
    python scripts/sync_gads_diario.py --dias 30   # reprocessa mais dias
    python scripts/sync_gads_diario.py --cliente source_of
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from db.cron_log import registrar_run
from google_ads.loader import carregar_gads

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def carregar_clientes() -> dict:
    with open(BASE_DIR / "clientes.json") as f:
        return json.load(f)["clientes"]


def sync_cliente(cliente_key: str, cfg: dict, dias: int) -> bool:
    """Executa sync para um cliente. Retorna True se sucesso."""
    customer_id = cfg.get("gads_customer_id")
    if not customer_id:
        logger.warning("[%s] sem gads_customer_id — pulando", cliente_key)
        return False

    logger.info("[%s] Iniciando sync Google Ads (customer_id=%s, dias=%d)",
                cliente_key, customer_id, dias)

    hoje = datetime.utcnow().strftime("%Y-%m-%d")
    run_id = registrar_run(
        cliente_key=cliente_key,
        periodo="diario",
        data_inicio=hoje,
        data_fim=hoje,
        status="iniciado",
    )

    try:
        linhas, periodo = carregar_gads(
            customer_id=customer_id,
            dias=dias,
            append=True,  # nunca limpa — só insere/atualiza
        )
        registrar_run(
            cliente_key=cliente_key,
            periodo="diario",
            data_inicio=hoje,
            data_fim=hoje,
            status="sucesso",
            run_id=run_id,
        )
        logger.info("[%s] ✅ %d linhas | período: %s", cliente_key, linhas, periodo)
        return True

    except Exception as exc:
        registrar_run(
            cliente_key=cliente_key,
            periodo="diario",
            data_inicio=hoje,
            data_fim=hoje,
            status="erro",
            erro=str(exc),
            run_id=run_id,
        )
        logger.error("[%s] ❌ Erro no sync: %s", cliente_key, exc)
        return False


def main():
    parser = argparse.ArgumentParser(description="Sync diário Google Ads → SQLite")
    parser.add_argument("--dias",    type=int, default=8,
                        help="Janela de dias a puxar (padrão: 8)")
    parser.add_argument("--cliente", type=str, default=None,
                        help="Executa só para um cliente específico")
    args = parser.parse_args()

    clientes = carregar_clientes()
    sucessos = 0
    erros    = 0

    for key, cfg in clientes.items():
        if not cfg.get("ativo", False):
            logger.info("[%s] inativo — pulando", key)
            continue
        if cfg.get("plataforma") != "google_ads":
            logger.info("[%s] plataforma=%s — pulando", key, cfg.get("plataforma"))
            continue
        if args.cliente and key != args.cliente:
            continue

        ok = sync_cliente(key, cfg, dias=args.dias)
        if ok:
            sucessos += 1
        else:
            erros += 1

    logger.info("Sync concluído: %d sucesso(s), %d erro(s)", sucessos, erros)
    sys.exit(1 if erros > 0 and sucessos == 0 else 0)


if __name__ == "__main__":
    main()
