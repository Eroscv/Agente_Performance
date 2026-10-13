"""
db/connection.py

Gerencia a conexão SQLite principal com row_factory e helpers de consulta.
A partir da Etapa 3, delega para db/connections.py (multi-DB federado).

Exporta:
  - get_conn()         → conexão federada (v_performance, campanhas_unificada, shopify)
  - query(sql, params) → list[dict]
  - query_one(sql, params) → dict | None
  - DB_PATH            → mantido para compatibilidade (aponta para campanhas.db legado)
"""

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"  # legado — mantído para compat


def get_conn():
    """Retorna conexão federada via db.connections.get_federated_conn()."""
    from db.connections import get_federated_conn
    return get_federated_conn()


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Executa SELECT e retorna lista de dicts."""
    from db.connections import query_fed
    return query_fed(sql, params)


def query_one(sql: str, params: tuple = ()) -> Optional[dict]:
    """Executa SELECT e retorna primeiro resultado ou None."""
    from db.connections import query_fed_one
    return query_fed_one(sql, params)

