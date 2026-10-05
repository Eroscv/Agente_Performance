"""
scripts/cron_shopify_sync.py

Cron de sincronização automática Shopify → SQLite + BigQuery.
Roda para todos os clientes com Shopify configurado (source_of e pod_kombucha).

Estratégia:
  - Por padrão sincroniza os últimos 8 dias (7 + margem para pedidos tardios)
  - Se a tabela estiver vazia, sincroniza os últimos 90 dias
  - Registra resultado em cron_runs + dual-write BQ

Uso:
    python scripts/cron_shopify_sync.py                           # todos os clientes, últimos 8 dias
    python scripts/cron_shopify_sync.py --dias 30                 # todos os clientes, 30 dias
    python scripts/cron_shopify_sync.py --cliente source_of       # apenas source_of
    python scripts/cron_shopify_sync.py --cliente pod_kombucha    # apenas POD
    python scripts/cron_shopify_sync.py --full                    # desde jan/2023
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Garante que o projeto raiz está no PATH
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cron_shopify_sync")


def _tabela_vazia(cliente_key: str = "source_of") -> bool:
    """Verifica se shopify_receita_diaria está vazia para o cliente."""
    try:
        from shopify.sync import status_sync
        status = status_sync(cliente_key=cliente_key)
        return not status.get("sincronizado", False)
    except Exception:
        return True


CLIENTES_SHOPIFY = [
    {"key": "source_of",   "token_env": "SHOPIFY_TOKEN",       "full_inicio": date(2023, 1, 1)},
    {"key": "pod_kombucha", "token_env": "SHOPIFY_POD_TOKEN",  "full_inicio": date(2024, 1, 1)},
    {"key": "botica",      "token_env": "SHOPIFY_BOTICA_TOKEN", "full_inicio": date(2024, 1, 1)},
]


def sync_cliente(cfg: dict, dias: int, full: bool) -> bool:
    """Executa sync Shopify para um cliente. Retorna True se sucesso."""
    from shopify.sync import sincronizar, status_sync

    cliente_key = cfg["key"]
    token_env   = cfg["token_env"]
    full_inicio = cfg["full_inicio"]

    token = os.getenv(token_env, "")
    if not token:
        logger.error("[%s] %s não configurado — sync abortado.", cliente_key, token_env)
        return False

    if full:
        d_inicio = full_inicio
        d_fim    = date.today()
    else:
        _dias = dias
        if _tabela_vazia(cliente_key):
            logger.info("[%s] Tabela vazia — fazendo carga inicial (90 dias)...", cliente_key)
            _dias = 90
        d_fim    = date.today()
        d_inicio = d_fim - timedelta(days=_dias - 1)

    logger.info("[%s] Sincronizando Shopify: %s → %s", cliente_key, d_inicio, d_fim)
    try:
        resultado = sincronizar(d_inicio, d_fim, cliente_key=cliente_key)
        logger.info(
            "[%s] ✅ Sync OK: %d dias | R$ %.2f | %d pedidos",
            cliente_key,
            resultado["dias_sincronizados"],
            resultado["receita_total"],
            resultado["pedidos_total"],
        )
        try:
            from agent.cache import invalidar_tudo
            invalidar_tudo()
        except Exception:
            pass
        try:
            from db.sync_alerts import registrar_resultado
            registrar_resultado(cliente_key, "shopify", sucesso=True)
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error("[%s] ❌ Sync falhou: %s", cliente_key, e)
        try:
            from db.sync_alerts import registrar_resultado
            registrar_resultado(cliente_key, "shopify", sucesso=False, erro=str(e))
        except Exception:
            pass
        return False


def main(dias: int = 8, full: bool = False, cliente: str = None) -> None:
    clientes = [c for c in CLIENTES_SHOPIFY if not cliente or c["key"] == cliente]
    if not clientes:
        logger.error("Cliente '%s' não encontrado em CLIENTES_SHOPIFY.", cliente)
        sys.exit(1)

    sucessos = erros = 0
    for cfg in clientes:
        ok = sync_cliente(cfg, dias=dias, full=full)
        if ok:
            sucessos += 1
        else:
            erros += 1

    logger.info("Sync Shopify concluído: %d sucesso(s), %d erro(s)", sucessos, erros)
    if erros > 0 and sucessos == 0:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cron sync Shopify → SQLite + BQ")
    parser.add_argument("--dias",    type=int,  default=8)
    parser.add_argument("--full",    action="store_true")
    parser.add_argument("--cliente", type=str,  default=None,
                        help="Rodar apenas para um cliente (source_of | pod_kombucha)")
    args = parser.parse_args()
    main(dias=args.dias, full=args.full, cliente=args.cliente)
