"""
db/shopify_sync.py

Sincronização Shopify → SQLite para o Agente de Performance Guará.

Mantém a tabela `shopify_receita_diaria` com receita real por dia,
permitindo que o ROAS seja calculado com dados Shopify (não Meta).

Pipeline:
  1. Lê pedidos pagos do Shopify (cursor-based pagination)
  2. Agrega por data (dia)
  3. Upsert em `shopify_receita_diaria` (idempotente)
  4. Registra execução em `cron_runs`

Uso:
    python -m db.shopify_sync                        # sincroniza últimos 7 dias
    python -m db.shopify_sync --dias 30              # últimos 30 dias
    python -m db.shopify_sync --inicio 2026-06-01 --fim 2026-06-30  # período fixo
    python -m db.shopify_sync --full                 # desde jan/2023 (reprocessamento completo)

Importar diretamente:
    from db.shopify_sync import sincronizar, receita_por_dia, roas_real_periodo
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"

# DDL da tabela de receita Shopify
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS shopify_receita_diaria (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data            DATE    NOT NULL UNIQUE,
    receita         REAL    NOT NULL DEFAULT 0,
    pedidos         INTEGER NOT NULL DEFAULT 0,
    novos_clientes  INTEGER NOT NULL DEFAULT 0,
    recompradores   INTEGER NOT NULL DEFAULT 0,
    ticket_medio    REAL,
    total_desconto  REAL    NOT NULL DEFAULT 0,
    pedidos_acima_300 INTEGER NOT NULL DEFAULT 0,
    atualizado_em   TEXT    NOT NULL DEFAULT (datetime('now'))
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


# ── Setup ─────────────────────────────────────────────────────────────────────

def _garantir_tabela(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE)
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ── Sincronização ─────────────────────────────────────────────────────────────

def sincronizar(
    data_inicio: date,
    data_fim: date,
    dry_run: bool = False,
) -> dict:
    """
    Sincroniza receita Shopify para o período especificado.

    Retorna:
        {
            "dias_sincronizados": int,
            "receita_total": float,
            "pedidos_total": int,
            "periodo": {"inicio": str, "fim": str},
            "dry_run": bool,
        }
    """
    logger.info(
        "[ShopifySync] Sincronizando %s → %s (dry_run=%s)",
        data_inicio, data_fim, dry_run,
    )

    # Importa o cliente Shopify (falha cedo se credenciais ausentes)
    try:
        from shopify.client import _paginar_pedidos, _orders_count_por_cliente
    except ImportError as e:
        raise RuntimeError(
            "shopify.client não encontrado — verifique o ambiente."
        ) from e

    shopify_token = os.getenv("SHOPIFY_TOKEN", "")
    if not shopify_token:
        raise ValueError(
            "SHOPIFY_TOKEN não configurado. "
            "Adicione ao .env: SHOPIFY_TOKEN=shpat_..."
        )

    # ── 1. Coletar pedidos do Shopify ────────────────────────────────────────
    receita_por_dia: dict[str, float]   = defaultdict(float)
    pedidos_por_dia: dict[str, int]     = defaultdict(int)
    desconto_por_dia: dict[str, float]  = defaultdict(float)
    ped_300_por_dia: dict[str, int]     = defaultdict(int)
    clientes_por_dia: dict[str, set]    = defaultdict(set)
    pedidos_buffer: list[dict]          = []
    customer_ids_global: set[int]       = set()

    total_pedidos_lidos = 0
    for pedido in _paginar_pedidos(data_inicio, data_fim):
        total_pedidos_lidos += 1
        criado_em = pedido.get("created_at", "")[:10]  # "YYYY-MM-DD"
        if not criado_em:
            continue

        preco    = float(pedido.get("total_price") or 0)
        desconto = float(pedido.get("total_discounts") or 0)

        receita_por_dia[criado_em]  += preco
        pedidos_por_dia[criado_em]  += 1
        desconto_por_dia[criado_em] += desconto
        if preco > 300:
            ped_300_por_dia[criado_em] += 1

        cid = (pedido.get("customer") or {}).get("id")
        if cid:
            clientes_por_dia[criado_em].add(cid)
            customer_ids_global.add(cid)
            pedidos_buffer.append({"dia": criado_em, "cid": cid, "pedido": pedido})

    logger.info("[ShopifySync] %d pedidos lidos do Shopify", total_pedidos_lidos)

    if total_pedidos_lidos == 0:
        logger.warning(
            "[ShopifySync] Nenhum pedido encontrado para %s → %s",
            data_inicio, data_fim,
        )
        return {
            "dias_sincronizados": 0,
            "receita_total": 0.0,
            "pedidos_total": 0,
            "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
            "dry_run": dry_run,
        }

    # ── 2. Calcular novos clientes / recompradores por dia ───────────────────
    oc_map = _orders_count_por_cliente(list(customer_ids_global))

    novos_por_dia:   dict[str, int] = defaultdict(int)
    recomp_por_dia:  dict[str, int] = defaultdict(int)

    for item in pedidos_buffer:
        dia = item["dia"]
        cid = item["cid"]
        oc  = oc_map.get(cid, 1)
        if oc == 1:
            novos_por_dia[dia]  += 1
        else:
            recomp_por_dia[dia] += 1

    # ── 3. Consolidar por dia ─────────────────────────────────────────────────
    todos_os_dias = set(receita_por_dia.keys())
    rows: list[tuple] = []
    for dia_str in sorted(todos_os_dias):
        pedidos  = pedidos_por_dia[dia_str]
        receita  = round(receita_por_dia[dia_str], 2)
        novos    = novos_por_dia[dia_str]
        recomp   = recomp_por_dia[dia_str]
        ticket   = round(receita / pedidos, 2) if pedidos else None
        desconto = round(desconto_por_dia[dia_str], 2)
        p300     = ped_300_por_dia[dia_str]
        rows.append((dia_str, receita, pedidos, novos, recomp, ticket, desconto, p300))

    if dry_run:
        logger.info("[ShopifySync] DRY RUN — %d linhas seriam gravadas", len(rows))
        for r in rows[:5]:
            logger.info("  %s", r)
        return {
            "dias_sincronizados": len(rows),
            "receita_total": sum(r[1] for r in rows),
            "pedidos_total": sum(r[2] for r in rows),
            "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
            "dry_run": True,
        }

    # ── 4. Upsert no SQLite ───────────────────────────────────────────────────
    conn = _get_conn()
    _garantir_tabela(conn)

    conn.executemany(_UPSERT_SQL, rows)
    conn.commit()

    # ── 5. Registra em cron_runs ──────────────────────────────────────────────
    try:
        conn.execute(
            "INSERT INTO cron_runs (job, status, detalhes, executado_em) "
            "VALUES ('shopify_sync', 'ok', ?, datetime('now'))",
            (f"Período: {data_inicio} → {data_fim}, dias: {len(rows)}, pedidos: {total_pedidos_lidos}",),
        )
        conn.commit()
    except Exception:
        pass  # cron_runs pode não existir em todos os ambientes

    conn.close()

    receita_total = sum(r[1] for r in rows)
    pedidos_total = sum(r[2] for r in rows)

    logger.info(
        "[ShopifySync] ✅ %d dias sincronizados | R$ %.2f | %d pedidos",
        len(rows), receita_total, pedidos_total,
    )
    return {
        "dias_sincronizados": len(rows),
        "receita_total": round(receita_total, 2),
        "pedidos_total": pedidos_total,
        "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
        "dry_run": False,
    }


# ── Consultas ─────────────────────────────────────────────────────────────────

def receita_por_dia(
    data_inicio: date,
    data_fim: date,
) -> dict[str, float]:
    """
    Retorna dict {data_str: receita} para o período.
    Se não houver dados sincronizados, retorna {}.
    """
    conn = _get_conn()
    _garantir_tabela(conn)
    rows = conn.execute(
        "SELECT data, receita FROM shopify_receita_diaria "
        "WHERE data BETWEEN ? AND ? ORDER BY data ASC",
        (str(data_inicio), str(data_fim)),
    ).fetchall()
    conn.close()
    return {r["data"]: r["receita"] for r in rows}


def receita_periodo(
    data_inicio: date,
    data_fim: date,
) -> float:
    """
    Retorna receita total Shopify no período.
    Usa dados sincronizados (não bate na API).
    """
    conn = _get_conn()
    _garantir_tabela(conn)
    row = conn.execute(
        "SELECT COALESCE(SUM(receita), 0) AS total FROM shopify_receita_diaria "
        "WHERE data BETWEEN ? AND ?",
        (str(data_inicio), str(data_fim)),
    ).fetchone()
    conn.close()
    return float(row["total"]) if row else 0.0


def metricas_shopify_periodo(
    data_inicio: date,
    data_fim: date,
) -> dict:
    """
    Retorna KPIs Shopify agregados para o período a partir dos dados sincronizados.
    Não bate na API Shopify.

    Returns:
        {
            receita_total, total_pedidos, ticket_medio,
            novos_clientes, recompradores, taxa_recompra_pct,
            total_desconto, pedidos_acima_300,
            sincronizado: bool   (False se não há dados no período)
        }
    """
    conn = _get_conn()
    _garantir_tabela(conn)
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(receita), 0)          AS receita_total,
            COALESCE(SUM(pedidos), 0)           AS total_pedidos,
            COALESCE(SUM(novos_clientes), 0)    AS novos_clientes,
            COALESCE(SUM(recompradores), 0)     AS recompradores,
            COALESCE(SUM(total_desconto), 0)    AS total_desconto,
            COALESCE(SUM(pedidos_acima_300), 0) AS pedidos_acima_300,
            COUNT(*) AS dias_com_dados
        FROM shopify_receita_diaria
        WHERE data BETWEEN ? AND ?
        """,
        (str(data_inicio), str(data_fim)),
    ).fetchone()
    conn.close()

    if not row or row["dias_com_dados"] == 0:
        return {
            "receita_total":     0.0,
            "total_pedidos":     0,
            "ticket_medio":      None,
            "novos_clientes":    0,
            "recompradores":     0,
            "taxa_recompra_pct": None,
            "total_desconto":    0.0,
            "pedidos_acima_300": 0,
            "sincronizado":      False,
        }

    tp    = int(row["total_pedidos"])
    rec   = float(row["receita_total"])
    novos = int(row["novos_clientes"])
    recmp = int(row["recompradores"])

    return {
        "receita_total":     round(rec, 2),
        "total_pedidos":     tp,
        "ticket_medio":      round(rec / tp, 2) if tp else None,
        "novos_clientes":    novos,
        "recompradores":     recmp,
        "taxa_recompra_pct": round(recmp / tp * 100, 2) if tp else None,
        "total_desconto":    round(float(row["total_desconto"]), 2),
        "pedidos_acima_300": int(row["pedidos_acima_300"]),
        "sincronizado":      True,
    }


def roas_real_periodo(
    data_inicio: date,
    data_fim: date,
    investimento_meta: float,
) -> Optional[float]:
    """
    ROAS real = Receita Shopify sincronizada / Investimento ajustado Meta.

    Usa dados já sincronizados (sem chamada à API).
    Retorna None se não houver dados ou investimento == 0.
    """
    from relatorio.regras import spend_com_imposto
    invest_ajustado = spend_com_imposto(investimento_meta)
    if invest_ajustado == 0:
        return None
    receita = receita_periodo(data_inicio, data_fim)
    if receita == 0:
        return None
    return round(receita / invest_ajustado, 4)


def status_sync() -> dict:
    """
    Retorna informações sobre o estado da sincronização.
    Útil para o endpoint /shopify/status.
    """
    conn = _get_conn()
    _garantir_tabela(conn)
    row = conn.execute(
        """
        SELECT
            MIN(data)           AS data_minima,
            MAX(data)           AS data_maxima,
            COUNT(*)            AS dias_sincronizados,
            COALESCE(SUM(receita), 0)  AS receita_total,
            COALESCE(SUM(pedidos), 0)  AS pedidos_total,
            MAX(atualizado_em)  AS ultima_atualizacao
        FROM shopify_receita_diaria
        """
    ).fetchone()
    conn.close()

    if not row or not row["data_minima"]:
        return {
            "sincronizado": False,
            "mensagem": "Nenhum dado sincronizado ainda. Execute: python -m db.shopify_sync",
        }

    return {
        "sincronizado":       True,
        "data_minima":        row["data_minima"],
        "data_maxima":        row["data_maxima"],
        "dias_sincronizados": row["dias_sincronizados"],
        "receita_total":      round(float(row["receita_total"]), 2),
        "pedidos_total":      int(row["pedidos_total"]),
        "ultima_atualizacao": row["ultima_atualizacao"],
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Sincroniza receita Shopify → SQLite"
    )
    parser.add_argument("--dias",    type=int, default=7, help="Últimos N dias (padrão: 7)")
    parser.add_argument("--inicio",  type=str, help="Data inicial YYYY-MM-DD")
    parser.add_argument("--fim",     type=str, help="Data final YYYY-MM-DD")
    parser.add_argument("--full",    action="store_true", help="Reprocessamento desde jan/2023")
    parser.add_argument("--dry-run", action="store_true", help="Só mostra, não grava")
    args = parser.parse_args()

    if args.full:
        d_inicio = date(2023, 1, 1)
        d_fim    = date.today()
    elif args.inicio and args.fim:
        d_inicio = date.fromisoformat(args.inicio)
        d_fim    = date.fromisoformat(args.fim)
    else:
        d_fim    = date.today()
        d_inicio = d_fim - timedelta(days=args.dias - 1)

    resultado = sincronizar(d_inicio, d_fim, dry_run=args.dry_run)
    print(f"\n✅ Sincronização concluída:")
    print(f"   Período:           {resultado['periodo']['inicio']} → {resultado['periodo']['fim']}")
    print(f"   Dias sincronizados: {resultado['dias_sincronizados']}")
    print(f"   Receita total:      R$ {resultado['receita_total']:,.2f}")
    print(f"   Pedidos total:      {resultado['pedidos_total']}")
    if args.dry_run:
        print("   ⚠️  DRY RUN — nada foi gravado")
