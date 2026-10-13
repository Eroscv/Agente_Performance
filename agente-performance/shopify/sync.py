"""
shopify/sync.py

Sincronização e consultas Shopify do Agente de Performance Guará.
Multi-cliente: source_of e pod_kombucha (e qualquer cliente com SHOPIFY_*_STORE/TOKEN).

Funções principais:
  - sincronizar(data_inicio, data_fim, cliente_key)  → busca pedidos na API e salva no DB
  - metricas_shopify_periodo(data_inicio, data_fim, cliente_key)  → KPIs consolidados
  - roas_real_periodo(data_inicio, data_fim, investimento_meta, cliente_key)  → ROAS real
  - status_sync(cliente_key)              → status da última sincronização
  - carregar_shopify(dias, cliente_key)   → alias para uso pelo registry
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

# DB por cliente — source_of mantém path histórico
_DB_PATHS: dict[str, Path] = {
    "source_of":   BASE_DIR / "data" / "performance" / "source_of" / "shopify_receita.db",
    "pod_kombucha": BASE_DIR / "data" / "performance" / "pod_kombucha" / "shopify_receita.db",
}

# Compatibilidade: DB_PATH padrão aponta para source_of
DB_PATH = _DB_PATHS["source_of"]


def _db_path_for(cliente_key: str) -> Path:
    p = _DB_PATHS.get(cliente_key, BASE_DIR / "data" / "performance" / cliente_key / "shopify_receita.db")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

# DDL da tabela de receita Shopify
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS shopify_receita_diaria (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data            DATE    NOT NULL UNIQUE,
    receita         REAL    NOT NULL DEFAULT 0,
    frete           REAL    NOT NULL DEFAULT 0,
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
    (data, receita, frete, pedidos, novos_clientes, recompradores,
     ticket_medio, total_desconto, pedidos_acima_300, atualizado_em)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
ON CONFLICT(data) DO UPDATE SET
    receita           = excluded.receita,
    frete             = excluded.frete,
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


def _get_conn(cliente_key: str = "source_of") -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path_for(cliente_key)), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ── Sincronização ─────────────────────────────────────────────────────────────

def sincronizar(
    data_inicio: date,
    data_fim: date,
    dry_run: bool = False,
    cliente_key: str = "source_of",
) -> dict:
    """
    Sincroniza receita Shopify para o período especificado.

    Args:
        data_inicio: data de início do período
        data_fim: data de fim do período
        dry_run: se True, não grava no banco
        cliente_key: 'source_of' | 'pod_kombucha' | outros configurados

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
        "[ShopifySync/%s] Sincronizando %s → %s (dry_run=%s)",
        cliente_key, data_inicio, data_fim, dry_run,
    )

    # Obtém cliente contextualizado para a loja correta
    try:
        from shopify.client import get_client_for
        sh = get_client_for(cliente_key)
    except (ImportError, ValueError) as e:
        raise RuntimeError(
            f"[ShopifySync/{cliente_key}] Erro ao obter cliente Shopify: {e}"
        ) from e

    # ── 1. Coletar pedidos do Shopify ────────────────────────────────────────
    receita_por_dia: dict[str, float]   = defaultdict(float)
    frete_por_dia: dict[str, float]     = defaultdict(float)
    pedidos_por_dia: dict[str, int]     = defaultdict(int)
    desconto_por_dia: dict[str, float]  = defaultdict(float)
    ped_300_por_dia: dict[str, int]     = defaultdict(int)
    clientes_por_dia: dict[str, set]    = defaultdict(set)
    pedidos_buffer: list[dict]          = []
    customer_ids_global: set[int]       = set()

    total_pedidos_lidos = 0
    for pedido in sh._paginar_pedidos(data_inicio, data_fim):
        total_pedidos_lidos += 1
        criado_em = pedido.get("created_at", "")[:10]  # "YYYY-MM-DD"
        if not criado_em:
            continue

        total_p  = float(pedido.get("total_price") or 0)
        sub_p    = float(pedido.get("subtotal_price") or 0)
        desconto = float(pedido.get("total_discounts") or 0)
        # receita = subtotal_price (produtos após desconto, sem frete)
        # frete   = total_price - subtotal_price
        receita  = sub_p
        frete    = round(total_p - sub_p, 2)

        receita_por_dia[criado_em]  += receita
        frete_por_dia[criado_em]    += frete
        pedidos_por_dia[criado_em]  += 1
        desconto_por_dia[criado_em] += desconto
        if total_p > 300:
            ped_300_por_dia[criado_em] += 1

        cid = (pedido.get("customer") or {}).get("id")
        if cid:
            clientes_por_dia[criado_em].add(cid)
            customer_ids_global.add(cid)
            pedidos_buffer.append({"dia": criado_em, "cid": cid, "pedido": pedido})

    logger.info("[ShopifySync] %d pedidos lidos do Shopify", total_pedidos_lidos)

    if total_pedidos_lidos == 0:
        logger.warning(
            "[ShopifySync/%s] Nenhum pedido encontrado para %s → %s",
            cliente_key, data_inicio, data_fim,
        )
        return {
            "dias_sincronizados": 0,
            "receita_total": 0.0,
            "pedidos_total": 0,
            "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
            "dry_run": dry_run,
        }

    # ── 2. Calcular novos clientes / recompradores por dia ───────────────────
    oc_map = sh._orders_count_por_cliente(list(customer_ids_global))

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
        frete    = round(frete_por_dia[dia_str], 2)
        novos    = novos_por_dia[dia_str]
        recomp   = recomp_por_dia[dia_str]
        ticket   = round(receita / pedidos, 2) if pedidos else None
        desconto = round(desconto_por_dia[dia_str], 2)
        p300     = ped_300_por_dia[dia_str]
        rows.append((dia_str, receita, frete, pedidos, novos, recomp, ticket, desconto, p300))

    if dry_run:
        logger.info("[ShopifySync/%s] DRY RUN — %d linhas seriam gravadas", cliente_key, len(rows))
        for r in rows[:5]:
            logger.info("  %s", r)
        return {
            "dias_sincronizados": len(rows),
            "receita_total": sum(r[1] for r in rows),
            "pedidos_total": sum(r[3] for r in rows),
            "periodo": {"inicio": str(data_inicio), "fim": str(data_fim)},
            "dry_run": True,
        }

    # ── 4. Upsert no SQLite ───────────────────────────────────────────────────
    conn = _get_conn(cliente_key)
    _garantir_tabela(conn)

    conn.executemany(_UPSERT_SQL, rows)
    conn.commit()

    # ── 5. Dual-write: espelhar no BigQuery ───────────────────────────────────
    try:
        from db.bq_writer import write_shopify, enabled as bq_enabled
        if bq_enabled():
            bq_rows = [
                {
                    "data":              r[0],
                    "receita":           r[1],
                    "frete":             r[2],
                    "pedidos":           r[3],
                    "novos_clientes":    r[4],
                    "recompradores":     r[5],
                    "ticket_medio":      r[6],
                    "total_desconto":    r[7],
                    "pedidos_acima_300": r[8],
                }
                for r in rows
            ]
            write_shopify(bq_rows, cliente_key)
            logger.info("[ShopifySync/%s] BQ dual-write: %d linhas", cliente_key, len(bq_rows))
    except Exception as _bq_exc:
        logger.warning("[ShopifySync/%s] BQ dual-write falhou (silencioso): %s", cliente_key, _bq_exc)

    # ── 6. Registra em cron_runs ──────────────────────────────────────────────
    try:
        conn.execute(
            "INSERT INTO cron_runs (job, status, detalhes, executado_em) "
            "VALUES ('shopify_sync', 'ok', ?, datetime('now'))",
            (f"cliente={cliente_key} | Período: {data_inicio} → {data_fim}, dias: {len(rows)}, pedidos: {total_pedidos_lidos}",),
        )
        conn.commit()
    except Exception:
        pass  # cron_runs pode não existir em todos os ambientes

    conn.close()

    receita_total = sum(r[1] for r in rows)
    pedidos_total = sum(r[3] for r in rows)

    logger.info(
        "[ShopifySync/%s] ✅ %d dias sincronizados | R$ %.2f | %d pedidos",
        cliente_key, len(rows), receita_total, pedidos_total,
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
    cliente_key: str = "source_of",
) -> dict[str, float]:
    """
    Retorna dict {data_str: receita} para o período.
    Se não houver dados sincronizados, retorna {}.
    """
    conn = _get_conn(cliente_key)
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
    cliente_key: str = "source_of",
) -> float:
    """
    Retorna receita total Shopify no período.
    Usa dados sincronizados (não bate na API).
    """
    conn = _get_conn(cliente_key)
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
    cliente_key: str = "source_of",
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
    conn = _get_conn(cliente_key)
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
    cliente_key: str = "source_of",
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
    receita = receita_periodo(data_inicio, data_fim, cliente_key=cliente_key)
    if receita == 0:
        return None
    return round(receita / invest_ajustado, 4)


def status_sync(cliente_key: str = "source_of") -> dict:
    """
    Retorna informações sobre o estado da sincronização.
    Útil para o endpoint /shopify/status.
    """
    conn = _get_conn(cliente_key)
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
            "mensagem": f"Nenhum dado sincronizado ainda para {cliente_key}. Execute: carregar_shopify(cliente_key='{cliente_key}')",
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


def carregar_shopify(dias: int = 8, cliente_key: str = "source_of") -> dict:
    """
    Wrapper para uso como loader no registry.

    Sincroniza os últimos `dias` dias de receita Shopify.
    Se a tabela estiver vazia, faz carga inicial dos últimos 90 dias.

    Args:
        dias: quantidade de dias a sincronizar (padrão: 8)
        cliente_key: 'source_of' | 'pod_kombucha' | outros

    Returns:
        dict com resultado da sincronização
    """
    status = status_sync(cliente_key=cliente_key)
    if not status.get("sincronizado", False):
        logger.info("[Shopify/%s] Tabela vazia — carga inicial (90 dias)", cliente_key)
        dias = 90

    d_fim    = date.today()
    d_inicio = d_fim - timedelta(days=dias - 1)
    return sincronizar(d_inicio, d_fim, cliente_key=cliente_key)


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
