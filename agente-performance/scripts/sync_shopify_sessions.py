"""
scripts/sync_shopify_sessions.py

Sync diário de sessões Shopify (funil) → shopify_diario.

Usa ShopifyQL (API 2025-01) para buscar, por dia:
  - sessoes                   : total de sessões
  - sessoes_com_carrinho       : NULL — não disponível via ShopifyQL (campo reservado p/ futuro)
  - sessoes_checkout           : sessoes_that_reached_checkout
  - sessoes_compra             : derivado de round(sessoes × conversion_rate)
  - taxa_conversao             : conversion_rate (0.0–1.0)

Flag de bot: dia com sessoes > 3× mediana dos últimos 7 dias é marcado como is_bot_suspect=1.

Tabelas de destino:
  source_of    → data/performance/source_of/shopify.db     tabela: source_of_shopify_sessoes
  pod_kombucha → data/performance/pod_kombucha/shopify.db  tabela: pod_kombucha_shopify_sessoes

Uso manual:
    python scripts/sync_shopify_sessions.py
    python scripts/sync_shopify_sessions.py --cliente source_of --dias 30
    python scripts/sync_shopify_sessions.py --data-inicio 2026-10-01 --data-fim 2026-10-13
    python scripts/sync_shopify_sessions.py --dry-run
"""

import argparse
import json
import logging
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import os
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

API_VERSION = "2025-01"

# Mapeamento cliente → env vars + db de destino
_CLIENT_CFG = {
    "source_of": {
        "store_env":  "SHOPIFY_STORE",
        "token_env":  "SHOPIFY_TOKEN",
        "tabela":     "source_of_shopify_sessoes",
        "db_subpath": "performance/source_of/shopify.db",
    },
    "pod_kombucha": {
        "store_env":  "SHOPIFY_POD_STORE",
        "token_env":  "SHOPIFY_POD_TOKEN",
        "tabela":     "pod_kombucha_shopify_sessoes",
        "db_subpath": "performance/pod_kombucha/shopify.db",
    },
}

# DDL da tabela de sessões
_DDL = """
CREATE TABLE IF NOT EXISTS {tabela} (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    data                 TEXT NOT NULL UNIQUE,
    sessoes              INTEGER,
    sessoes_com_carrinho INTEGER,          -- NULL: não disponível via ShopifyQL
    sessoes_checkout     INTEGER,
    sessoes_compra       INTEGER,          -- derivado: round(sessoes × taxa_conversao)
    taxa_conversao       REAL,             -- 0.0–1.0
    is_bot_suspect       INTEGER DEFAULT 0, -- 1 se sessões > 3× mediana dos 7 dias anteriores
    atualizado_em        TEXT DEFAULT (datetime('now'))
)
"""


def _load_env():
    """Carrega .env sem depender do dotenv."""
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _shopifyql(store: str, token: str, ql_query: str) -> list[dict]:
    """
    Executa uma query ShopifyQL (API 2025-01) e retorna lista de dicts.
    Levanta RuntimeError em caso de parseErrors ou erro HTTP.
    """
    url = f"https://{store}.myshopify.com/admin/api/{API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    # Escapa aspas na query para embed em string GraphQL
    ql_escaped = ql_query.replace('"', '\\"')
    gql = (
        '{ shopifyqlQuery(query: "' + ql_escaped + '") { '
        'parseErrors tableData { columns { name dataType } rows } } }'
    )

    resp = requests.post(url, headers=headers, json={"query": gql}, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        raise RuntimeError(f"GraphQL error: {data['errors']}")

    result = (data.get("data") or {}).get("shopifyqlQuery") or {}
    parse_errors = result.get("parseErrors") or []
    if parse_errors:
        raise RuntimeError(f"ShopifyQL parseErrors: {parse_errors}")

    table_data = result.get("tableData")
    if not table_data:
        return []

    columns = [c["name"] for c in (table_data.get("columns") or [])]
    rows = table_data.get("rows") or []

    if not columns:
        return []

    return rows if isinstance(rows[0], dict) else [
        dict(zip(columns, r)) for r in rows
    ] if rows else []


def buscar_sessoes(
    store: str,
    token: str,
    date_start: date,
    date_end: date,
) -> list[dict]:
    """
    Busca sessões diárias via ShopifyQL (TIMESERIES day).

    Colunas ShopifyQL:
      - sessions
      - sessions_with_cart_additions   → sessoes_com_carrinho
      - sessions_that_reached_checkout → sessoes_checkout
      - sessions_that_completed_checkout → sessoes_compra (mais preciso que derivado)
      - conversion_rate (0.0–1.0)

    Nota: TIMESERIES é obrigatório para expor as colunas de funil.
          GROUP BY day não as retorna.
    """
    ql = (
        f"FROM sessions "
        f"SHOW sessions, sessions_with_cart_additions, "
        f"sessions_that_reached_checkout, sessions_that_completed_checkout, "
        f"conversion_rate "
        f"TIMESERIES day "
        f"SINCE {date_start} UNTIL {date_end}"
    )
    raw = _shopifyql(store, token, ql)
    rows = []
    for r in raw:
        sessoes   = int(r.get("sessions") or 0)
        carrinho  = int(r.get("sessions_with_cart_additions") or 0) or None
        checkout  = int(r.get("sessions_that_reached_checkout") or 0) or None
        compras   = int(r.get("sessions_that_completed_checkout") or 0) or None
        conv_rate = float(r.get("conversion_rate") or 0)
        rows.append({
            "data":                 r.get("day", "")[:10],
            "sessoes":              sessoes,
            "sessoes_com_carrinho": carrinho,
            "sessoes_checkout":     checkout,
            "sessoes_compra":       compras,
            "taxa_conversao":       round(conv_rate, 6),
        })
    return rows


def _detectar_bots(rows: list[dict], janela_dias: int = 7, fator: float = 3.0) -> list[dict]:
    """
    Marca is_bot_suspect=1 para dias onde sessoes > fator × mediana da janela anterior.
    Critério conservador: só suspeita, não remove.
    """
    import statistics

    sessoes_por_data: dict[str, int] = {r["data"]: r["sessoes"] for r in rows}
    datas = sorted(sessoes_por_data)
    suspeitos = set()

    for i, d in enumerate(datas):
        janela = [
            sessoes_por_data[datas[j]]
            for j in range(max(0, i - janela_dias), i)
        ]
        if len(janela) < 3:
            continue
        mediana = statistics.median(janela)
        if mediana > 0 and sessoes_por_data[d] > fator * mediana:
            suspeitos.add(d)
            logger.warning(
                "[bot_suspect] %s: %d sessões > %.0f× mediana(%.0f)",
                d, sessoes_por_data[d], fator, mediana,
            )

    for r in rows:
        r["is_bot_suspect"] = 1 if r["data"] in suspeitos else 0
    return rows


def salvar_sessoes(
    rows: list[dict],
    tabela: str,
    db_path: Path,
) -> int:
    """Persiste sessões diárias no SQLite (INSERT OR REPLACE)."""
    if not rows:
        return 0

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_DDL.format(tabela=tabela))

    cols = [
        "data", "sessoes", "sessoes_com_carrinho", "sessoes_checkout",
        "sessoes_compra", "taxa_conversao", "is_bot_suspect",
    ]
    sql = f"""
        INSERT OR REPLACE INTO {tabela} ({', '.join(cols)}, atualizado_em)
        VALUES ({', '.join(['?'] * len(cols))}, datetime('now'))
    """
    for r in rows:
        conn.execute(sql, [r.get(c) for c in cols])

    conn.commit()
    conn.close()
    logger.info("%d linhas salvas em %s (%s)", len(rows), tabela, db_path.name)
    return len(rows)


def sync_cliente(
    cliente_key: str,
    date_start: date,
    date_end: date,
    dry_run: bool = False,
) -> bool:
    """Executa sync de sessões para um cliente. Retorna True/False."""
    cfg = _CLIENT_CFG.get(cliente_key)
    if not cfg:
        logger.error("Cliente '%s' não configurado em sync_shopify_sessions.py", cliente_key)
        return False

    store = os.environ.get(cfg["store_env"], "").strip()
    token = os.environ.get(cfg["token_env"], "").strip()
    if not store or not token:
        logger.error(
            "[%s] %s ou %s não definido no .env",
            cliente_key, cfg["store_env"], cfg["token_env"],
        )
        return False

    tabela  = cfg["tabela"]
    db_path = BASE_DIR / "data" / cfg["db_subpath"]

    logger.info(
        "[%s] Buscando sessões %s → %s (store=%s)",
        cliente_key, date_start, date_end, store,
    )

    try:
        rows = buscar_sessoes(store, token, date_start, date_end)
    except Exception as exc:
        logger.error("[%s] Erro na API ShopifyQL: %s", cliente_key, exc)
        return False

    if not rows:
        logger.warning("[%s] API retornou 0 linhas para o período", cliente_key)
        return False

    # Detecção de bots (janela de 14 dias para ter contexto)
    rows = _detectar_bots(rows, janela_dias=14, fator=3.0)
    bots = [r for r in rows if r.get("is_bot_suspect")]
    if bots:
        logger.warning(
            "[%s] %d dia(s) suspeito(s) de bot: %s",
            cliente_key, len(bots), [b["data"] for b in bots],
        )

    if dry_run:
        logger.info("[%s] DRY RUN — %d linhas, não gravadas", cliente_key, len(rows))
        for r in rows:
            bot = " ⚠️ BOT?" if r.get("is_bot_suspect") else ""
            logger.info(
                "  %s: sessoes=%d carrinho=%s checkout=%s compras=%s conv=%.2f%%%s",
                r["data"], r["sessoes"],
                r["sessoes_com_carrinho"] or 0,
                r["sessoes_checkout"] or 0,
                r["sessoes_compra"] or 0,
                (r["taxa_conversao"] or 0) * 100, bot,
            )
        return True

    salvas = salvar_sessoes(rows, tabela=tabela, db_path=db_path)
    logger.info(
        "[%s] ✅ %d dias salvos | %s → %s",
        cliente_key, salvas, date_start, date_end,
    )
    return True


def main():
    _load_env()

    parser = argparse.ArgumentParser(
        description="Sync diário de sessões Shopify → SQLite"
    )
    parser.add_argument(
        "--cliente", type=str, default=None,
        help="Chave do cliente (padrão: source_of + pod_kombucha)",
    )
    parser.add_argument(
        "--dias", type=int, default=8,
        help="Dias a puxar contando para trás a partir de ontem (padrão: 8)",
    )
    parser.add_argument(
        "--data-inicio", type=str, default=None,
        help="Data início YYYY-MM-DD (ignora --dias se informado)",
    )
    parser.add_argument(
        "--data-fim", type=str, default=None,
        help="Data fim YYYY-MM-DD (padrão: ontem)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Busca e exibe dados sem gravar no banco",
    )
    args = parser.parse_args()

    # Resolve período
    ontem = date.today() - timedelta(days=1)
    date_end   = date.fromisoformat(args.data_fim)   if args.data_fim   else ontem
    date_start = date.fromisoformat(args.data_inicio) if args.data_inicio else (
        date_end - timedelta(days=args.dias - 1)
    )

    if date_end > ontem:
        logger.warning(
            "data_fim %s é futura — ajustando para ontem (%s)", date_end, ontem
        )
        date_end = ontem

    clientes = [args.cliente] if args.cliente else list(_CLIENT_CFG)

    sucessos = erros = 0
    for key in clientes:
        ok = sync_cliente(key, date_start, date_end, dry_run=args.dry_run)
        if ok:
            sucessos += 1
        else:
            erros += 1

    logger.info(
        "Sync sessões concluído: %d sucesso(s), %d erro(s)", sucessos, erros
    )
    sys.exit(1 if erros > 0 and sucessos == 0 else 0)


if __name__ == "__main__":
    main()
