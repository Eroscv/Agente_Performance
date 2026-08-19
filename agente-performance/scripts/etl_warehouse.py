#!/usr/bin/env python3
"""
ETL Warehouse — scripts/etl_warehouse.py
Lê SQLite (Meta Ads, Google Ads, Shopify) e grava Parquet particionado por mês.

Modos:
  --full          Reprocessa todo o histórico
  --incremental   Só dias novos desde o último run (default)
  --month 2026-08 Reprocessa mês específico
  --date 2026-08-18 Reprocessa dia específico
"""

import os
import sys
import json
import logging
import argparse
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# Adiciona raiz do projeto ao path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

WH_DIR     = ROOT / "data" / "warehouse"
PERF_DIR   = WH_DIR / "fato_performance"
ADS_DIR    = WH_DIR / "fato_ads"
SHOP_DIR   = WH_DIR / "fato_shopify"
STATE_FILE = WH_DIR / "_etl_state.json"
DATA_DIR   = ROOT / "data" / "performance"

# ── Helpers ───────────────────────────────────────────────────────────────────

OBJETIVO_MAP = {
    "OUTCOME_SALES":      "conversao",
    "OUTCOME_AWARENESS":  "awareness",
    "OUTCOME_ENGAGEMENT": "engajamento",
    "LINK_CLICKS":        "trafego",
    "SEARCH":             "search",
    "Search":             "search",
    "SHOPPING":           "shopping",
    "Shopping":           "shopping",
    "PERFORMANCE_MAX":    "pmax",
    "DEMAND_GEN":         "demand_gen",
}


def normalizar_objetivo(obj: Optional[str]) -> str:
    if obj is None:
        return "desconhecido"
    return OBJETIVO_MAP.get(obj, obj.lower())


def campanha_sk(cliente_id: str, plataforma: str, campaign_id: Optional[str], campanha: str) -> str:
    """Surrogate key para dim_campanha."""
    import re
    if campaign_id:
        parte = campaign_id
    else:
        parte = re.sub(r"[^a-z0-9]", "_", campanha.lower())[:40]
    return f"{cliente_id}__{plataforma}__{parte}"


def safe_div(a, b):
    """Divisão segura retorna None se denominador zero ou None."""
    if not b or b == 0:
        return None
    return a / b if a is not None else None


def meses_no_range(data_ini: date, data_fim: date) -> list:
    """Retorna lista de (ano, mes) no intervalo."""
    meses = []
    cur = data_ini.replace(day=1)
    while cur <= data_fim:
        meses.append((cur.year, cur.month))
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)
    return meses


def carregar_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def salvar_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


# ── ETL Meta Ads ──────────────────────────────────────────────────────────────

def etl_meta_ads(cliente_key: str, ano: int, mes: int, conn: duckdb.DuckDBPyConnection):
    """Processa um mês de dados Meta Ads para fato_performance e fato_ads."""
    db_path  = DATA_DIR / cliente_key / "meta_ads.db"
    if not db_path.exists():
        logger.warning("[%s] meta_ads.db não encontrado — skip", cliente_key)
        return 0, 0

    tabela   = f"{cliente_key}_meta_ads_diario"
    mes_str  = f"{ano:04d}-{mes:02d}"
    data_ini = f"{mes_str}-01"
    if mes == 12:
        data_fim = f"{ano + 1:04d}-01-01"
    else:
        data_fim = f"{ano:04d}-{mes + 1:02d}-01"

    conn.execute(f"ATTACH IF NOT EXISTS '{db_path}' AS meta_{cliente_key} (READ_ONLY)")
    etl_ts = datetime.utcnow().isoformat()

    # ── fato_performance (nível campanha, agregado) ──
    perf_file = PERF_DIR / f"fato_performance_{mes_str}.parquet"
    # Carrega existente se houver (outros clientes/plataformas já gravados)
    existing_perf = []
    if perf_file.exists():
        existing_perf = conn.execute(
            f"SELECT * FROM read_parquet('{perf_file}') WHERE NOT (cliente_id = '{cliente_key}' AND plataforma = 'meta_ads' AND strftime(data, '%Y-%m') = '{mes_str}')"
        ).fetchdf()

    novas_perf = conn.execute(f"""
        SELECT
            CAST(data AS DATE)                                              AS data,
            {campanha_sk.__name__}_col                                      AS campanha_sk,
            campaign_id,
            campanha,
            '{cliente_key}'                                                 AS cliente_id,
            'meta_ads'                                                      AS plataforma,
            SUM(investimento)                                               AS investimento,
            SUM(valor_compras)                                              AS receita,
            SUM(impressoes)                                                 AS impressoes,
            SUM(cliques_link)                                               AS cliques,
            SUM(alcance)                                                    AS alcance,
            SUM(compras)                                                    AS conversoes,
            SUM(video_3s)                                                   AS video_3s,
            SUM(thruplays)                                                  AS thruplays,
            NULL::DOUBLE                                                    AS adicoes_carrinho,
            NULL::DOUBLE                                                    AS checkout_iniciado,
            CASE WHEN SUM(investimento) > 0
                 THEN SUM(valor_compras) / SUM(investimento) END            AS roas,
            CASE WHEN SUM(compras) > 0
                 THEN SUM(investimento) / SUM(compras) END                  AS cpa,
            CASE WHEN SUM(impressoes) > 0
                 THEN SUM(cliques_link) / SUM(impressoes) * 100 END         AS ctr,
            CASE WHEN SUM(cliques_link) > 0
                 THEN SUM(investimento) / SUM(cliques_link) END             AS cpc_medio,
            '{etl_ts}'                                                      AS _etl_ts,
            '{db_path}'                                                     AS _source_db
        FROM (
            SELECT *,
                   '{cliente_key}' || '__meta_ads__' ||
                   COALESCE(campaign_id, md5(campanha)[:16])
                   AS campanha_sk_col
            FROM meta_{cliente_key}.{tabela}
            WHERE data >= '{data_ini}' AND data < '{data_fim}'
              AND investimento > 0
        )
        GROUP BY data, campanha_sk_col, campaign_id, campanha
    """).fetchdf()

    # Remove coluna auxiliar se existir
    if "campanha_sk_col" in novas_perf.columns:
        novas_perf = novas_perf.drop(columns=["campanha_sk_col"])

    linhas_perf = len(novas_perf)
    logger.info("[%s] meta fato_performance %s: %d linhas", cliente_key, mes_str, linhas_perf)

    # ── fato_ads (nível anúncio) ──
    ads_file = ADS_DIR / f"fato_ads_{mes_str}.parquet"
    existing_ads = []
    if ads_file.exists():
        existing_ads = conn.execute(
            f"SELECT * FROM read_parquet('{ads_file}') WHERE NOT (cliente_id = '{cliente_key}' AND strftime(data, '%Y-%m') = '{mes_str}')"
        ).fetchdf()

    novas_ads = conn.execute(f"""
        SELECT
            CAST(data AS DATE)  AS data,
            ad_id,
            '{cliente_key}' || '__meta_ads__' ||
                COALESCE(campaign_id, md5(campanha)[:16])
                                AS campanha_sk,
            '{cliente_key}'     AS cliente_id,
            campaign_id,
            adset_id,
            adset_name,
            ad_name,
            investimento,
            valor_compras       AS receita,
            impressoes,
            cliques_link        AS cliques,
            alcance,
            compras             AS conversoes,
            video_3s,
            thruplays,
            adicoes_carrinho,
            checkout_iniciado,
            CASE WHEN investimento > 0 THEN valor_compras / investimento END AS roas,
            CASE WHEN compras > 0 THEN investimento / compras END            AS cpa,
            CASE WHEN impressoes > 0 THEN cliques_link / impressoes * 100 END AS ctr,
            qualidade_ranking,
            engagement_ranking,
            conversion_ranking,
            '{etl_ts}'          AS _etl_ts,
            '{db_path}'         AS _source_db
        FROM meta_{cliente_key}.{tabela}
        WHERE data >= '{data_ini}' AND data < '{data_fim}'
          AND investimento > 0
    """).fetchdf()

    linhas_ads = len(novas_ads)
    logger.info("[%s] meta fato_ads %s: %d linhas", cliente_key, mes_str, linhas_ads)

    return novas_perf, novas_ads, linhas_perf, linhas_ads


# ── ETL Google Ads ────────────────────────────────────────────────────────────

def etl_google_ads(cliente_key: str, ano: int, mes: int, conn: duckdb.DuckDBPyConnection):
    """Processa um mês de dados Google Ads para fato_performance."""
    db_path = DATA_DIR / cliente_key / "google_ads.db"
    if not db_path.exists():
        logger.warning("[%s] google_ads.db não encontrado — skip", cliente_key)
        return None, 0

    tabela  = f"{cliente_key}_gads_diario"
    mes_str = f"{ano:04d}-{mes:02d}"
    data_ini = f"{mes_str}-01"
    if mes == 12:
        data_fim = f"{ano + 1:04d}-01-01"
    else:
        data_fim = f"{ano:04d}-{mes + 1:02d}-01"

    conn.execute(f"ATTACH IF NOT EXISTS '{db_path}' AS gads_{cliente_key} (READ_ONLY)")
    etl_ts = datetime.utcnow().isoformat()

    try:
        novas_perf = conn.execute(f"""
            SELECT
                CAST(data AS DATE)  AS data,
                '{cliente_key}' || '__google_ads__' || campaign_id AS campanha_sk,
                campaign_id,
                campanha,
                '{cliente_key}'     AS cliente_id,
                'google_ads'        AS plataforma,
                investimento,
                valor_conversao     AS receita,
                impressoes,
                cliques,
                NULL::DOUBLE        AS alcance,
                conversoes,
                NULL::DOUBLE        AS video_3s,
                NULL::DOUBLE        AS thruplays,
                NULL::DOUBLE        AS adicoes_carrinho,
                NULL::DOUBLE        AS checkout_iniciado,
                CASE WHEN investimento > 0 THEN valor_conversao / investimento END AS roas,
                CASE WHEN conversoes > 0 THEN investimento / conversoes END        AS cpa,
                ctr,
                cpc_medio,
                '{etl_ts}'          AS _etl_ts,
                '{db_path}'         AS _source_db
            FROM gads_{cliente_key}.{tabela}
            WHERE data >= '{data_ini}' AND data < '{data_fim}'
              AND investimento > 0
        """).fetchdf()
        linhas = len(novas_perf)
        logger.info("[%s] gads fato_performance %s: %d linhas", cliente_key, mes_str, linhas)
        return novas_perf, linhas
    except Exception as e:
        logger.warning("[%s] gads skip %s: %s", cliente_key, mes_str, e)
        return None, 0


# ── ETL Shopify ───────────────────────────────────────────────────────────────

def etl_shopify(ano: int, mes: int, conn: duckdb.DuckDBPyConnection):
    """Processa um mês de dados Shopify."""
    db_path = DATA_DIR / "source_of" / "shopify_receita.db"
    if not db_path.exists():
        logger.warning("shopify_receita.db não encontrado — skip")
        return None, 0

    mes_str  = f"{ano:04d}-{mes:02d}"
    data_ini = f"{mes_str}-01"
    if mes == 12:
        data_fim = f"{ano + 1:04d}-01-01"
    else:
        data_fim = f"{ano:04d}-{mes + 1:02d}-01"

    etl_ts = datetime.utcnow().isoformat()
    conn.execute(f"ATTACH IF NOT EXISTS '{db_path}' AS shopify_src (READ_ONLY)")

    try:
        # Descobre nome da tabela
        tabelas = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'shopify_src'").fetchall()
        if not tabelas:
            # fallback para sqlite_master
            import sqlite3 as _sq
            _c = _sq.connect(str(db_path))
            tabelas = [(r[0],) for r in _c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
            _c.close()
        if not tabelas:
            return None, 0
        tabela = tabelas[0][0]

        df = conn.execute(f"""
            SELECT
                CAST(data AS DATE)  AS data,
                'source_of'         AS cliente_id,
                receita,
                pedidos,
                novos_clientes,
                recompradores,
                ticket_medio,
                total_desconto,
                pedidos_acima_300,
                CASE WHEN pedidos > 0 THEN CAST(recompradores AS DOUBLE) / pedidos * 100 END AS taxa_recompra,
                '{etl_ts}'          AS _etl_ts,
                '{db_path}'         AS _source_db
            FROM shopify_src.{tabela}
            WHERE data >= '{data_ini}' AND data < '{data_fim}'
        """).fetchdf()
        logger.info("shopify fato_shopify %s: %d linhas", mes_str, len(df))
        return df, len(df)
    except Exception as e:
        logger.warning("shopify skip %s: %s", mes_str, e)
        return None, 0


# ── Dimensões ─────────────────────────────────────────────────────────────────

def gerar_dim_tempo(conn: duckdb.DuckDBPyConnection):
    """Gera dim_tempo de 2024-01-01 a 2030-12-31."""
    out = WH_DIR / "dim_tempo.parquet"
    conn.execute(f"""
        COPY (
            SELECT
                d::DATE                                    AS data,
                year(d)                                    AS ano,
                month(d)                                   AS mes,
                CASE month(d)
                    WHEN 1  THEN 'Janeiro'   WHEN 2  THEN 'Fevereiro'
                    WHEN 3  THEN 'Março'     WHEN 4  THEN 'Abril'
                    WHEN 5  THEN 'Maio'      WHEN 6  THEN 'Junho'
                    WHEN 7  THEN 'Julho'     WHEN 8  THEN 'Agosto'
                    WHEN 9  THEN 'Setembro'  WHEN 10 THEN 'Outubro'
                    WHEN 11 THEN 'Novembro'  WHEN 12 THEN 'Dezembro'
                END                                        AS mes_nome,
                quarter(d)                                 AS trimestre,
                weekofyear(d)                              AS semana_iso,
                dayofmonth(d)                              AS dia_mes,
                isodow(d)                                  AS dia_semana,
                CASE isodow(d)
                    WHEN 1 THEN 'Segunda'  WHEN 2 THEN 'Terça'
                    WHEN 3 THEN 'Quarta'   WHEN 4 THEN 'Quinta'
                    WHEN 5 THEN 'Sexta'    WHEN 6 THEN 'Sábado'
                    WHEN 7 THEN 'Domingo'
                END                                        AS dia_semana_nome,
                isodow(d) >= 6                             AS fim_de_semana,
                strftime(d, '%Y-%m')                       AS ano_mes,
                strftime(d, '%Y-W') || lpad(CAST(weekofyear(d) AS VARCHAR), 2, '0') AS ano_semana
            FROM generate_series(DATE '2024-01-01', DATE '2030-12-31', INTERVAL 1 DAY) t(d)
        ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    logger.info("dim_tempo gerado: %s", out)


def gerar_dim_cliente(conn: duckdb.DuckDBPyConnection):
    """Gera dim_cliente a partir do clientes.json."""
    import json as _json
    cfg_path = ROOT / "clientes.json"
    clientes = _json.loads(cfg_path.read_text())["clientes"]
    etl_ts = datetime.utcnow().isoformat()

    rows = []
    for key, cfg in clientes.items():
        plats = cfg.get("plataformas", {})
        rows.append({
            "cliente_id":   key,
            "nome":         cfg.get("nome", key),
            "slack_channel": cfg.get("slack_label", ""),
            "slack_emoji":  cfg.get("slack_emoji", ""),
            "tem_meta":     plats.get("meta_ads", {}).get("ativo", False),
            "tem_gads":     plats.get("google_ads", {}).get("ativo", False),
            "tem_shopify":  plats.get("shopify", {}).get("ativo", False),
            "ativo":        cfg.get("ativo", True),
            "_etl_ts":      etl_ts,
        })

    import pandas as pd
    df = pd.DataFrame(rows)
    out = WH_DIR / "dim_cliente.parquet"
    conn.execute(f"COPY (SELECT * FROM df) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    logger.info("dim_cliente gerado: %d clientes", len(rows))


def gerar_dim_campanha(conn: duckdb.DuckDBPyConnection):
    """Gera dim_campanha varrendo todos os Parquet de fato_performance."""
    perf_glob = str(PERF_DIR / "*.parquet")
    if not list(PERF_DIR.glob("*.parquet")):
        logger.warning("dim_campanha: nenhum fato_performance encontrado — skip")
        return

    out = WH_DIR / "dim_campanha.parquet"
    etl_ts = datetime.utcnow().isoformat()
    conn.execute(f"""
        COPY (
            SELECT
                campanha_sk,
                ANY_VALUE(campaign_id)    AS campaign_id,
                ANY_VALUE(campanha)       AS campanha,
                cliente_id,
                plataforma,
                '{etl_ts}'               AS _etl_ts
            FROM read_parquet('{perf_glob}', union_by_name=true)
            GROUP BY campanha_sk, cliente_id, plataforma
        ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n = conn.execute(f"SELECT COUNT(*) FROM read_parquet('{out}', union_by_name=true)").fetchone()[0]
    logger.info("dim_campanha gerado: %d campanhas", n)


# ── Gravar Parquet ─────────────────────────────────────────────────────────────

def gravar_parquet(conn, df, out_path: Path):
    """Grava DataFrame como Parquet via DuckDB."""
    if df is None or len(df) == 0:
        return 0
    conn.execute(f"COPY (SELECT * FROM df) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    return len(df)


# ── Orquestração principal ─────────────────────────────────────────────────────

def processar_mes(ano: int, mes: int, clientes: list, conn: duckdb.DuckDBPyConnection) -> dict:
    """Processa todos os clientes para um mês. Retorna stats."""
    mes_str  = f"{ano:04d}-{mes:02d}"
    perf_file = PERF_DIR / f"fato_performance_{mes_str}.parquet"
    ads_file  = ADS_DIR  / f"fato_ads_{mes_str}.parquet"
    shop_file = SHOP_DIR / f"fato_shopify_{mes_str}.parquet"

    import pandas as pd

    all_perf = []
    all_ads  = []

    for cliente_key in clientes:
        # Meta Ads
        try:
            result = etl_meta_ads(cliente_key, ano, mes, conn)
            if result and result[0] is not None and len(result[0]) > 0:
                all_perf.append(result[0])
            if result and result[1] is not None and len(result[1]) > 0:
                all_ads.append(result[1])
        except Exception as e:
            logger.error("[%s] meta etl erro %s: %s", cliente_key, mes_str, e)

        # Google Ads
        try:
            gads_df, _ = etl_google_ads(cliente_key, ano, mes, conn)
            if gads_df is not None and len(gads_df) > 0:
                all_perf.append(gads_df)
        except Exception as e:
            logger.error("[%s] gads etl erro %s: %s", cliente_key, mes_str, e)

    # Shopify
    try:
        shop_df, _ = etl_shopify(ano, mes, conn)
        if shop_df is not None and len(shop_df) > 0:
            gravar_parquet(conn, shop_df, shop_file)
    except Exception as e:
        logger.error("shopify etl erro %s: %s", mes_str, e)

    # Grava fato_performance
    n_perf = 0
    if all_perf:
        df_perf = pd.concat(all_perf, ignore_index=True)
        n_perf = gravar_parquet(conn, df_perf, perf_file)

    # Grava fato_ads
    n_ads = 0
    if all_ads:
        df_ads = pd.concat(all_ads, ignore_index=True)
        n_ads = gravar_parquet(conn, df_ads, ads_file)

    logger.info("[%s] fato_performance=%d fato_ads=%d", mes_str, n_perf, n_ads)
    return {"mes": mes_str, "fato_performance": n_perf, "fato_ads": n_ads}


def resolver_range(args, state: dict):
    """Resolve data_ini / data_fim a partir dos argumentos CLI."""
    if args.date:
        d = date.fromisoformat(args.date)
        return d, d
    if args.month:
        ano, mes = map(int, args.month.split("-"))
        import calendar
        _, ultimo_dia = calendar.monthrange(ano, mes)
        return date(ano, mes, 1), date(ano, mes, ultimo_dia)
    if args.full:
        return date(2024, 1, 1), date.today() - timedelta(days=1)
    # incremental: desde o dia seguinte ao último run
    ultimo = state.get("ultimo_dia_processado")
    if ultimo:
        ini = date.fromisoformat(ultimo) + timedelta(days=1)
    else:
        ini = date(2024, 1, 1)
    fim = date.today() - timedelta(days=1)
    if ini > fim:
        logger.info("Nada a processar — já atualizado até %s", fim)
        return None, None
    return ini, fim


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ETL Warehouse — SQLite → Parquet")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full",        action="store_true", help="Reprocessa todo histórico")
    mode.add_argument("--incremental", action="store_true", help="Só dias novos (default)")
    mode.add_argument("--month",       metavar="YYYY-MM",   help="Reprocessa mês específico")
    mode.add_argument("--date",        metavar="YYYY-MM-DD",help="Reprocessa dia específico")
    args = parser.parse_args()

    # Garante dirs
    for d in [WH_DIR, PERF_DIR, ADS_DIR, SHOP_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    state = carregar_state()
    data_ini, data_fim = resolver_range(args, state)
    if data_ini is None:
        return

    logger.info("ETL range: %s → %s", data_ini, data_fim)

    conn = duckdb.connect()
    conn.execute("INSTALL json; LOAD json")
    conn.execute("INSTALL parquet; LOAD parquet")

    # Clientes com dados
    clientes = ["source_of", "pod_kombucha"]

    # Gera dimensões fixas
    gerar_dim_tempo(conn)
    gerar_dim_cliente(conn)

    # Processa meses
    meses = meses_no_range(data_ini, data_fim)
    stats = []
    for ano, mes in meses:
        s = processar_mes(ano, mes, clientes, conn)
        stats.append(s)

    # Gera dim_campanha após fatos
    gerar_dim_campanha(conn)

    # Conta totais
    def contar(glob_pattern):
        files = list(Path(glob_pattern).parent.glob(Path(glob_pattern).name))
        if not files: return 0
        return conn.execute(f"SELECT COUNT(*) FROM read_parquet('{glob_pattern}')").fetchone()[0]

    total_perf  = contar(str(PERF_DIR / "*.parquet"))
    total_ads   = contar(str(ADS_DIR  / "*.parquet"))
    total_shop  = contar(str(SHOP_DIR / "*.parquet"))
    total_dim_c = contar(str(WH_DIR   / "dim_campanha.parquet")) if (WH_DIR / "dim_campanha.parquet").exists() else 0

    state_novo = {
        "ultimo_run":            datetime.utcnow().isoformat(),
        "ultimo_run_status":     "ok",
        "ultimo_dia_processado": str(data_fim),
        "linhas_por_tabela": {
            "fato_performance": total_perf,
            "fato_ads":         total_ads,
            "fato_shopify":     total_shop,
            "dim_campanha":     total_dim_c,
        },
        "meses_processados": stats,
    }
    salvar_state(state_novo)

    logger.info("ETL concluido: perf=%d ads=%d shopify=%d dim_camp=%d",
                total_perf, total_ads, total_shop, total_dim_c)
    conn.close()


if __name__ == "__main__":
    main()
