"""
shopify/sync_pod.py

Sincronização Shopify → SQLite para POD Kombucha.
DB separado: data/performance/pod_kombucha/shopify_receita.db
"""
from __future__ import annotations

import logging
import os
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "performance" / "pod_kombucha" / "shopify_receita.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS shopify_receita_diaria (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    data              DATE    NOT NULL UNIQUE,
    receita           REAL    NOT NULL DEFAULT 0,
    pedidos           INTEGER NOT NULL DEFAULT 0,
    novos_clientes    INTEGER NOT NULL DEFAULT 0,
    recompradores     INTEGER NOT NULL DEFAULT 0,
    ticket_medio      REAL,
    total_desconto    REAL    NOT NULL DEFAULT 0,
    pedidos_acima_300 INTEGER NOT NULL DEFAULT 0,
    atualizado_em     TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_UPSERT_SQL = """
INSERT INTO shopify_receita_diaria
    (data, receita, pedidos, novos_clientes, recompradores,
     ticket_medio, total_desconto, pedidos_acima_300, atualizado_em)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
ON CONFLICT(data) DO UPDATE SET
    receita           = excluded.receita,
    pedidos           = excluded.pedidos,
    novos_clientes    = excluded.novos_clientes,
    recompradores     = excluded.recompradores,
    ticket_medio      = excluded.ticket_medio,
    total_desconto    = excluded.total_desconto,
    pedidos_acima_300 = excluded.pedidos_acima_300,
    atualizado_em     = datetime('now')
;
"""


def _garantir_tabela(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE)
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def sincronizar(data_inicio: date, data_fim: date, dry_run: bool = False) -> dict:
    """Sincroniza receita Shopify POD para o período especificado."""
    from shopify.client_pod import _paginar_pedidos, _orders_count_por_cliente

    token = os.getenv("SHOPIFY_POD_TOKEN", "")
    if not token:
        raise ValueError("SHOPIFY_POD_TOKEN não configurado. Adicione ao .env.")

    receita_por_dia:  dict = defaultdict(float)
    pedidos_por_dia:  dict = defaultdict(int)
    desconto_por_dia: dict = defaultdict(float)
    ped_300_por_dia:  dict = defaultdict(int)
    clientes_por_dia: dict = defaultdict(set)
    pedidos_buffer:   list = []
    customer_ids_global: set = set()

    for pedido in _paginar_pedidos(data_inicio, data_fim):
        criado_em = pedido.get("created_at", "")[:10]
        if not criado_em:
            continue

        preco    = float(pedido.get("total_price") or 0)
        desconto = float(pedido.get("total_discounts") or 0)

        receita_por_dia[criado_em]  += preco
        pedidos_por_dia[criado_em]  += 1
        desconto_por_dia[criado_em] += desconto
        if preco >= 300:
            ped_300_por_dia[criado_em] += 1

        cid = (pedido.get("customer") or {}).get("id")
        if cid:
            clientes_por_dia[criado_em].add(cid)
            customer_ids_global.add(cid)
        pedidos_buffer.append({"data": criado_em, "customer_id": cid})

    # Recompras
    oc_map = _orders_count_por_cliente(customer_ids_global, data_inicio)

    recompra_por_dia: dict = defaultdict(int)
    for p in pedidos_buffer:
        if p["customer_id"] and oc_map.get(p["customer_id"], 0) > 0:
            recompra_por_dia[p["data"]] += 1

    if dry_run:
        return {
            "dias_sincronizados": len(receita_por_dia),
            "receita_total":      sum(receita_por_dia.values()),
            "pedidos_total":      sum(pedidos_por_dia.values()),
            "dry_run":            True,
        }

    conn = _get_conn()
    _garantir_tabela(conn)

    dias_gravados = 0
    for d_str, receita in sorted(receita_por_dia.items()):
        peds   = pedidos_por_dia[d_str]
        ticket = receita / peds if peds > 0 else 0
        novos  = max(len(clientes_por_dia[d_str]) - recompra_por_dia[d_str], 0)
        conn.execute(_UPSERT_SQL, (
            d_str, receita, peds, novos,
            recompra_por_dia[d_str],
            ticket,
            desconto_por_dia[d_str],
            ped_300_por_dia[d_str],
        ))
        dias_gravados += 1

    conn.commit()
    conn.close()

    logger.info(
        "[pod_shopify] Sync OK: %d dias | R$ %.2f | %d pedidos",
        dias_gravados, sum(receita_por_dia.values()), sum(pedidos_por_dia.values()),
    )
    return {
        "dias_sincronizados": dias_gravados,
        "receita_total":      sum(receita_por_dia.values()),
        "pedidos_total":      sum(pedidos_por_dia.values()),
        "periodo":            {"inicio": str(data_inicio), "fim": str(data_fim)},
        "dry_run":            False,
    }


def status_sync() -> dict:
    """Retorna status da última sincronização."""
    if not DB_PATH.exists():
        return {"sincronizado": False}
    try:
        conn = _get_conn()
        r = conn.execute(
            "SELECT MAX(data) ultima_data, COUNT(*) dias, SUM(receita) receita_total "
            "FROM shopify_receita_diaria"
        ).fetchone()
        conn.close()
        return {
            "sincronizado":  bool(r and r["ultima_data"]),
            "ultima_data":   r["ultima_data"] if r else None,
            "dias":          r["dias"] if r else 0,
            "receita_total": r["receita_total"] if r else 0,
        }
    except Exception:
        return {"sincronizado": False}
