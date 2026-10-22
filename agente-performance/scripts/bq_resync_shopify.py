#!/usr/bin/env python3
"""
scripts/bq_resync_shopify.py

Re-sincroniza shopify_diario no BigQuery a partir do SQLite local.

Usa o SQLite como fonte de verdade (novos_clientes já calculado com
orders_count real via customers.json) e força um MERGE em todos os
dias do período informado — sem bater na Shopify API.

Uso:
    # Re-sync completo (todos os dias disponíveis)
    python scripts/bq_resync_shopify.py

    # Período específico
    python scripts/bq_resync_shopify.py --inicio 2026-09-21 --fim 2026-10-22

    # Dry-run: mostra o que seria enviado, sem gravar no BQ
    python scripts/bq_resync_shopify.py --dry-run

    # Outro cliente
    python scripts/bq_resync_shopify.py --cliente pod_kombucha
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

# Garante que o diretório raiz do projeto está no sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bq_resync_shopify")

_DB_PATHS = {
    "source_of":   ROOT / "data" / "performance" / "source_of" / "shopify_receita.db",
    "pod_kombucha": ROOT / "data" / "performance" / "pod_kombucha" / "shopify_receita.db",
}


def _ler_sqlite(
    cliente_key: str,
    data_inicio: date | None,
    data_fim: date | None,
) -> list[dict]:
    db_path = _DB_PATHS.get(
        cliente_key,
        ROOT / "data" / "performance" / cliente_key / "shopify_receita.db",
    )
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite não encontrado: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if data_inicio and data_fim:
        sql = (
            "SELECT * FROM shopify_receita_diaria "
            "WHERE data BETWEEN ? AND ? ORDER BY data"
        )
        rows = conn.execute(sql, (str(data_inicio), str(data_fim))).fetchall()
    elif data_inicio:
        sql = "SELECT * FROM shopify_receita_diaria WHERE data >= ? ORDER BY data"
        rows = conn.execute(sql, (str(data_inicio),)).fetchall()
    elif data_fim:
        sql = "SELECT * FROM shopify_receita_diaria WHERE data <= ? ORDER BY data"
        rows = conn.execute(sql, (str(data_fim),)).fetchall()
    else:
        sql = "SELECT * FROM shopify_receita_diaria ORDER BY data"
        rows = conn.execute(sql).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def _montar_bq_rows(sqlite_rows: list[dict], cliente: str) -> list[dict]:
    """
    Converte linhas do SQLite para o formato esperado pelo write_shopify do bq_writer.
    write_shopify já faz round() + str() internamente — passamos valores numéricos crus.
    """
    bq_rows = []
    for r in sqlite_rows:
        bq_rows.append({
            "data":               r["data"],
            "receita":            float(r.get("receita") or 0),
            "frete":              float(r.get("frete") or 0),
            "pedidos":            int(r.get("pedidos") or 0),
            "novos_clientes":     int(r.get("novos_clientes") or 0),
            "recompradores":      int(r.get("recompradores") or 0),
            "ticket_medio":       float(r["ticket_medio"]) if r.get("ticket_medio") is not None else None,
            "total_desconto":     float(r.get("total_desconto") or 0),
            "pedidos_acima_300":  int(r.get("pedidos_acima_300") or 0),
            "receita_criada":     float(r.get("receita_criada") or 0),
            "pedidos_criados":    int(r.get("pedidos_criados") or 0),
            "pedidos_meta":       int(r.get("pedidos_meta") or 0),
            "receita_meta":       float(r.get("receita_meta") or 0),
            "novos_meta":         int(r.get("novos_meta") or 0),
            "pedidos_google":     int(r.get("pedidos_google") or 0),
            "receita_google":     float(r.get("receita_google") or 0),
            "novos_google":       int(r.get("novos_google") or 0),
            "pedidos_outros":     int(r.get("pedidos_outros") or 0),
            "receita_outros":     float(r.get("receita_outros") or 0),
            "novos_outros":       int(r.get("novos_outros") or 0),
            "gross_sales":        float(r.get("gross_sales") or 0),
            "pedidos_gt300":      int(r.get("pedidos_gt300") or 0),
        })
    return bq_rows


def main():
    parser = argparse.ArgumentParser(
        description="Re-sincroniza shopify_diario no BQ a partir do SQLite local"
    )
    parser.add_argument("--inicio",  type=str, help="Data inicial YYYY-MM-DD (padrão: início do SQLite)")
    parser.add_argument("--fim",     type=str, help="Data final   YYYY-MM-DD (padrão: fim do SQLite)")
    parser.add_argument("--cliente", type=str, default="source_of", help="cliente_key (padrão: source_of)")
    parser.add_argument("--dry-run", action="store_true", help="Mostra dados sem gravar no BQ")
    parser.add_argument("--batch",   type=int, default=500, help="Linhas por MERGE (padrão: 500)")
    args = parser.parse_args()

    d_inicio = date.fromisoformat(args.inicio) if args.inicio else None
    d_fim    = date.fromisoformat(args.fim)    if args.fim    else None

    logger.info("🔍 Lendo SQLite — cliente=%s | %s → %s", args.cliente, d_inicio or "início", d_fim or "hoje")
    try:
        sqlite_rows = _ler_sqlite(args.cliente, d_inicio, d_fim)
    except FileNotFoundError as e:
        logger.error("❌ %s", e)
        sys.exit(1)

    if not sqlite_rows:
        logger.warning("⚠️  Nenhuma linha encontrada no SQLite para o período informado.")
        sys.exit(0)

    logger.info("📦 %d dias lidos do SQLite (%s → %s)", len(sqlite_rows), sqlite_rows[0]["data"], sqlite_rows[-1]["data"])

    # Mostrar amostra dos novos_clientes para validação rápida
    sample_days = [r for r in sqlite_rows if r["data"] in (
        "2026-10-18", "2026-10-17", "2026-10-16", "2026-10-15", "2026-10-14",
    )]
    if sample_days:
        logger.info("📊 Amostra novos_clientes (dias-alvo):")
        for r in sample_days:
            logger.info(
                "   %s | pedidos=%d | novos_clientes=%d | recompradores=%d",
                r["data"], r["pedidos"], r["novos_clientes"], r["recompradores"],
            )

    bq_rows = _montar_bq_rows(sqlite_rows, args.cliente)

    if args.dry_run:
        logger.info("🔍 DRY-RUN — %d linhas seriam enviadas ao BQ. Primeiras 5:", len(bq_rows))
        for r in bq_rows[:5]:
            logger.info(
                "   %s | receita=%s | pedidos=%d | novos=%d | recomp=%d",
                r["data"], r["receita"], r["pedidos"], r["novos_clientes"], r["recompradores"],
            )
        logger.info("✅ Dry-run concluído. Nada foi gravado.")
        return

    # Verifica BQ disponível
    from db.bq_writer import enabled, write_shopify
    if not enabled():
        logger.error("❌ BQ não está configurado (GOOGLE_APPLICATION_CREDENTIALS ausente ou inválido)")
        sys.exit(1)

    # Envia em batches para evitar timeout no MERGE
    total = len(bq_rows)
    enviados = 0
    erros = 0
    batch_size = args.batch

    logger.info("🚀 Iniciando re-sync BQ — %d linhas em batches de %d", total, batch_size)

    for i in range(0, total, batch_size):
        batch = bq_rows[i:i + batch_size]
        d0 = batch[0]["data"]
        d1 = batch[-1]["data"]
        logger.info("  Batch %d/%d — %s → %s (%d linhas)", i // batch_size + 1, -(-total // batch_size), d0, d1, len(batch))
        try:
            write_shopify(batch, args.cliente)
            enviados += len(batch)
        except Exception as exc:
            import traceback as _tb
            logger.error("  ❌ Erro no batch %s → %s: %s", d0, d1, exc)
            _tb.print_exc()
            erros += len(batch)

    logger.info("")
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info("✅ Re-sync concluído")
    logger.info("   Linhas enviadas: %d", enviados)
    logger.info("   Erros:           %d", erros)
    logger.info("   Período:         %s → %s", sqlite_rows[0]["data"], sqlite_rows[-1]["data"])
    if erros:
        logger.warning("⚠️  %d linhas com erro — verifique logs acima", erros)
    else:
        logger.info("🎯 BQ shopify_diario/%s agora espelha o SQLite 100%%", args.cliente)


if __name__ == "__main__":
    main()
