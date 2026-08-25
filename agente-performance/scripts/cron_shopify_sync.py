"""
scripts/cron_shopify_sync.py

Cron de sincronização automática Shopify → SQLite.
Chamado diariamente pelo OpenClaw para manter receita_shopify_diaria atualizado.

Estratégia:
  - Por padrão sincroniza os últimos 8 dias (7 + margem para pedidos tardios)
  - Se a tabela estiver vazia, sincroniza os últimos 90 dias
  - Registra resultado em cron_runs

Uso:
    python scripts/cron_shopify_sync.py            # últimos 8 dias
    python scripts/cron_shopify_sync.py --dias 30  # últimos 30 dias
    python scripts/cron_shopify_sync.py --full     # desde jan/2023
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


def _tabela_vazia() -> bool:
    """Verifica se shopify_receita_diaria está vazia."""
    try:
        from db.shopify_sync import status_sync
        status = status_sync()
        return not status.get("sincronizado", False)
    except Exception:
        return True


def main(dias: int = 8, full: bool = False) -> None:
    from db.shopify_sync import sincronizar

    shopify_token = os.getenv("SHOPIFY_TOKEN", "")
    if not shopify_token:
        logger.error("SHOPIFY_TOKEN não configurado — sync abortado.")
        sys.exit(1)

    # Se tabela vazia, faz carga inicial dos últimos 90 dias
    if not full and _tabela_vazia():
        logger.info("Tabela vazia — fazendo carga inicial (últimos 90 dias)...")
        dias = 90

    if full:
        d_inicio = date(2023, 1, 1)
        d_fim    = date.today()
    else:
        d_fim    = date.today()
        d_inicio = d_fim - timedelta(days=dias - 1)

    logger.info("Sincronizando Shopify: %s → %s", d_inicio, d_fim)

    try:
        resultado = sincronizar(d_inicio, d_fim)
        logger.info(
            "✅ Sync OK: %d dias | R$ %.2f | %d pedidos",
            resultado["dias_sincronizados"],
            resultado["receita_total"],
            resultado["pedidos_total"],
        )
        # Invalida cache e registra sucesso
        try:
            from agent.cache import invalidar_tudo
            invalidar_tudo()
        except Exception:
            pass
        try:
            from db.sync_alerts import registrar_resultado
            registrar_resultado("source_of", "shopify", sucesso=True)
        except Exception:
            pass
    except Exception as e:
        logger.error("❌ Sync falhou: %s", e)
        try:
            from db.sync_alerts import registrar_resultado
            registrar_resultado("source_of", "shopify", sucesso=False, erro=str(e))
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cron sync Shopify → SQLite")
    parser.add_argument("--dias", type=int, default=8)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    main(dias=args.dias, full=args.full)
