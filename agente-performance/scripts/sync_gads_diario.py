"""
scripts/sync_gads_diario.py

Sync diário automático — Google Ads API → SQLite.

Executa para todos os clientes ativos com plataforma google_ads ativa
e gads_customer_id definido em clientes.json.

Comportamento:
  - Puxa os últimos 8 dias (append=True) para cobrir dados atrasados
  - Grava na tabela {cliente_key}_gads_diario de cada cliente
  - Registra execução em cron_runs
  - Loga resultado no stdout

Uso manual:
    python scripts/sync_gads_diario.py
    python scripts/sync_gads_diario.py --dias 30
    python scripts/sync_gads_diario.py --cliente source_of
"""

import argparse
import json
import logging
import sys
from datetime import datetime, UTC
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from db.cron_log import registrar_run
from google_ads.loader import carregar_gads
from db.connections import PERFORMANCE_DBS

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
    """Executa sync Google Ads para um cliente. Retorna True se sucesso."""
    plataformas = cfg.get("plataformas", {})
    gads_cfg    = plataformas.get("google_ads", {})

    if not gads_cfg.get("ativo", False):
        logger.info("[%s] google_ads inativo — pulando", cliente_key)
        return None  # None = pulado, não é erro

    customer_id = gads_cfg.get("gads_customer_id")
    if not customer_id:
        logger.warning("[%s] sem gads_customer_id — pulando", cliente_key)
        return None  # None = pulado

    tabela = gads_cfg.get("tabela_db", f"{cliente_key}_gads_diario")

    logger.info("[%s] Iniciando sync Google Ads (customer_id=%s, tabela=%s, dias=%d)",
                cliente_key, customer_id, tabela, dias)

    hoje = datetime.now(UTC).strftime("%Y-%m-%d")
    run_id = registrar_run(
        cliente_key=cliente_key,
        periodo="diario",
        data_inicio=hoje,
        data_fim=hoje,
        status="iniciado",
    )

    try:
        # Banco federado correto para este cliente/plataforma
        fed_path = PERFORMANCE_DBS.get((cliente_key, "google_ads"))
        db_path  = str(fed_path) if fed_path else None

        linhas, periodo = carregar_gads(
            customer_id=customer_id,
            dias=dias,
            append=False,  # usa DELETE por período antes de inserir (permite atualização retroativa)
            tabela=tabela,
            db_path=db_path,
            cliente_key=cliente_key,
        )
        registrar_run(
            cliente_key=cliente_key,
            periodo="diario",
            data_inicio=hoje,
            data_fim=hoje,
            status="sucesso",
            run_id=run_id,
        )
        logger.info("[%s] ✅ %d linhas | período: %s | tabela: %s",
                    cliente_key, linhas, periodo, tabela)
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
            logger.info("[%s] cliente inativo — pulando", key)
            continue
        if args.cliente and key != args.cliente:
            continue

        ok = sync_cliente(key, cfg, dias=args.dias)
        if ok is True:
            sucessos += 1
        elif ok is None:
            pass  # pulado (inativo ou sem customer_id)
        else:
            erros += 1

    logger.info("Sync concluído: %d sucesso(s), %d erro(s)", sucessos, erros)
    sys.exit(1 if erros > 0 and sucessos == 0 else 0)


if __name__ == "__main__":
    main()
