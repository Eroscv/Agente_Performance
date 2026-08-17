"""
scripts/migrar_tabelas.py

Migração: tabelas legadas → estrutura separada por cliente + fonte.

Cria:
  source_of_meta_ads_diario
  source_of_gads_diario
  source_of_shopify_diario
  pod_kombucha_gads_diario

Migra dados das tabelas legadas.
Recria views v_performance e campanhas_unificada.
"""

import logging
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── DDL das novas tabelas ────────────────────────────────────────────────────

DDL = """

-- Source OF · Meta Ads
CREATE TABLE IF NOT EXISTS source_of_meta_ads_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data            TEXT NOT NULL,
    campanha        TEXT,
    campaign_id     TEXT,
    adset_name      TEXT,
    ad_name         TEXT,
    ad_id           TEXT,
    objetivo        TEXT,
    status_criativo TEXT,
    impressoes      REAL DEFAULT 0,
    alcance         REAL DEFAULT 0,
    cliques_link    REAL DEFAULT 0,
    cliques_todos   REAL DEFAULT 0,
    investimento    REAL DEFAULT 0,
    compras         REAL DEFAULT 0,
    valor_compras   REAL DEFAULT 0,
    cpa_meta        REAL,
    roas_compras    REAL,
    cpm             REAL,
    frequencia      REAL,
    video_3s        REAL DEFAULT 0,
    video_50pct     REAL DEFAULT 0,
    thruplays       REAL DEFAULT 0,
    adicoes_carrinho    REAL DEFAULT 0,
    checkout_iniciado   REAL DEFAULT 0,
    visualizacoes_landing REAL DEFAULT 0,
    UNIQUE(data, campanha, ad_id)
);
CREATE INDEX IF NOT EXISTS idx_sof_meta_data     ON source_of_meta_ads_diario(data);
CREATE INDEX IF NOT EXISTS idx_sof_meta_campanha ON source_of_meta_ads_diario(campanha);
CREATE INDEX IF NOT EXISTS idx_sof_meta_data_camp ON source_of_meta_ads_diario(data, campanha);

-- Source OF · Google Ads
CREATE TABLE IF NOT EXISTS source_of_gads_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data            TEXT NOT NULL,
    campanha        TEXT,
    campaign_id     TEXT,
    tipo_campanha   TEXT,
    status          TEXT,
    impressoes      REAL DEFAULT 0,
    cliques         REAL DEFAULT 0,
    investimento    REAL DEFAULT 0,
    conversoes      REAL DEFAULT 0,
    valor_conversao REAL DEFAULT 0,
    ctr             REAL,
    cpc_medio       REAL,
    UNIQUE(data, campaign_id)
);
CREATE INDEX IF NOT EXISTS idx_sof_gads_data     ON source_of_gads_diario(data);
CREATE INDEX IF NOT EXISTS idx_sof_gads_campanha ON source_of_gads_diario(campanha);
CREATE INDEX IF NOT EXISTS idx_sof_gads_data_camp ON source_of_gads_diario(data, campaign_id);

-- Source OF · Shopify
CREATE TABLE IF NOT EXISTS source_of_shopify_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data            TEXT NOT NULL UNIQUE,
    receita         REAL DEFAULT 0,
    pedidos         INTEGER DEFAULT 0,
    novos_clientes  INTEGER DEFAULT 0,
    recompradores   INTEGER DEFAULT 0,
    ticket_medio    REAL,
    total_desconto  REAL DEFAULT 0,
    pedidos_acima_300 INTEGER DEFAULT 0,
    atualizado_em   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sof_shopify_data ON source_of_shopify_diario(data);

-- POD Kombucha · Google Ads
CREATE TABLE IF NOT EXISTS pod_kombucha_gads_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data            TEXT NOT NULL,
    campanha        TEXT,
    campaign_id     TEXT,
    tipo_campanha   TEXT,
    impressoes      REAL DEFAULT 0,
    cliques         REAL DEFAULT 0,
    investimento    REAL DEFAULT 0,
    conversoes      REAL DEFAULT 0,
    valor_conversao REAL DEFAULT 0,
    ctr             REAL,
    cpc_medio       REAL,
    UNIQUE(data, campanha, campaign_id)
);
CREATE INDEX IF NOT EXISTS idx_pod_gads_data     ON pod_kombucha_gads_diario(data);
CREATE INDEX IF NOT EXISTS idx_pod_gads_campanha ON pod_kombucha_gads_diario(campanha);
CREATE INDEX IF NOT EXISTS idx_pod_gads_data_camp ON pod_kombucha_gads_diario(data, campaign_id);

"""

# ── View v_performance ───────────────────────────────────────────────────────

VIEW_PERFORMANCE = """
CREATE VIEW v_performance AS

-- Source OF · Google Ads
SELECT
    'source_of'         AS cliente_key,
    'Source OF'         AS cliente_nome,
    'google_ads'        AS plataforma,
    data,
    campanha,
    campaign_id,
    tipo_campanha,
    status,
    COALESCE(impressoes, 0)      AS impressoes,
    COALESCE(cliques, 0)         AS cliques,
    NULL                         AS alcance,
    COALESCE(investimento, 0)    AS investimento,
    COALESCE(conversoes, 0)      AS conversoes,
    COALESCE(valor_conversao, 0) AS receita,
    CASE WHEN investimento > 0
         THEN ROUND(valor_conversao / investimento, 4)
         ELSE NULL END           AS roas,
    CASE WHEN conversoes > 0
         THEN ROUND(investimento / conversoes, 2)
         ELSE NULL END           AS cpa,
    ctr,
    cpc_medio,
    NULL AS video_3s,
    NULL AS thruplays
FROM source_of_gads_diario

UNION ALL

-- Source OF · Meta Ads
SELECT
    'source_of'         AS cliente_key,
    'Source OF'         AS cliente_nome,
    'meta_ads'          AS plataforma,
    data,
    campanha,
    campaign_id,
    objetivo            AS tipo_campanha,
    status_criativo     AS status,
    COALESCE(impressoes, 0)      AS impressoes,
    COALESCE(cliques_link, 0)    AS cliques,
    COALESCE(alcance, 0)         AS alcance,
    COALESCE(investimento, 0)    AS investimento,
    COALESCE(compras, 0)         AS conversoes,
    COALESCE(valor_compras, 0)   AS receita,
    CASE WHEN investimento * 1.1215 > 0
         THEN ROUND(valor_compras / (investimento * 1.1215), 4)
         ELSE NULL END           AS roas,
    CASE WHEN compras > 0
         THEN ROUND(investimento * 1.1215 / compras, 2)
         ELSE NULL END           AS cpa,
    CASE WHEN impressoes > 0
         THEN ROUND(cliques_link / impressoes * 100.0, 4)
         ELSE NULL END           AS ctr,
    NULL                         AS cpc_medio,
    COALESCE(video_3s, 0)        AS video_3s,
    COALESCE(thruplays, 0)       AS thruplays
FROM source_of_meta_ads_diario

UNION ALL

-- POD Kombucha · Google Ads
SELECT
    'pod_kombucha'      AS cliente_key,
    'POD Kombucha'      AS cliente_nome,
    'google_ads'        AS plataforma,
    data,
    campanha,
    campaign_id,
    tipo_campanha,
    NULL                         AS status,
    COALESCE(impressoes, 0)      AS impressoes,
    COALESCE(cliques, 0)         AS cliques,
    NULL                         AS alcance,
    COALESCE(investimento, 0)    AS investimento,
    COALESCE(conversoes, 0)      AS conversoes,
    COALESCE(valor_conversao, 0) AS receita,
    CASE WHEN investimento > 0
         THEN ROUND(valor_conversao / investimento, 4)
         ELSE NULL END           AS roas,
    CASE WHEN conversoes > 0
         THEN ROUND(investimento / conversoes, 2)
         ELSE NULL END           AS cpa,
    ctr,
    cpc_medio,
    NULL AS video_3s,
    NULL AS thruplays
FROM pod_kombucha_gads_diario
"""

# ── View campanhas_unificada (retrocompatibilidade) ──────────────────────────

VIEW_UNIFICADA = """
CREATE VIEW campanhas_unificada AS
SELECT
    data            AS data_inicio,
    data            AS data_fim,
    campanha,
    cliente_key,
    cliente_nome,
    plataforma,
    investimento,
    receita         AS valor_compras,
    conversoes      AS compras,
    impressoes,
    cliques         AS cliques_link,
    video_3s,
    thruplays,
    roas            AS roas_compras,
    cpa             AS custo_por_compra,
    ctr             AS ctr_link,
    alcance,
    NULL AS frequencia,
    NULL AS cpm,
    NULL AS compras_app,
    NULL AS compras_site,
    NULL AS cliques_todos,
    NULL AS cpc_link,
    NULL AS visualizacoes_landing,
    NULL AS reproducoes_video,
    NULL AS conversas_iniciadas,
    NULL AS adicoes_carrinho,
    NULL AS checkout_iniciado
FROM v_performance
"""


def migrar(conn: sqlite3.Connection) -> None:

    # 1. Cria tabelas novas
    logger.info("Criando tabelas novas...")
    conn.executescript(DDL)
    conn.commit()

    # 2. Migra Source OF · Meta Ads
    #    Fonte: ads_diario (granularidade anúncio, mai-ago 2026)
    logger.info("Migrando source_of_meta_ads_diario a partir de ads_diario...")
    conn.execute("""
        INSERT OR IGNORE INTO source_of_meta_ads_diario (
            data, campanha, campaign_id, adset_name, ad_name, ad_id,
            objetivo, status_criativo,
            impressoes, cliques_link, investimento,
            compras, valor_compras, cpa_meta, roas_compras,
            video_3s, video_50pct, thruplays
        )
        SELECT
            data_inicio, campanha, campaign_id, adset_name, ad_name, ad_id,
            objetivo, status_criativo,
            COALESCE(impressoes, 0), COALESCE(cliques_link, 0), COALESCE(investimento, 0),
            COALESCE(compras, 0), COALESCE(valor_compras, 0), cpa_meta, roas_compras,
            COALESCE(video_3s, 0), COALESCE(video_50pct, 0), COALESCE(thruplays, 0)
        FROM ads_diario
    """)
    n1 = conn.execute("SELECT COUNT(*) FROM source_of_meta_ads_diario").fetchone()[0]

    #    Fonte: campanhas (histórico csv 2024-2026, granularidade campanha/dia)
    #    Insere apenas onde não há linha em ads_diario para a mesma data/campanha
    logger.info("Migrando source_of_meta_ads_diario a partir de campanhas (histórico)...")
    conn.execute("""
        INSERT OR IGNORE INTO source_of_meta_ads_diario (
            data, campanha,
            impressoes, alcance, cliques_link, cliques_todos,
            investimento, compras, valor_compras,
            roas_compras, cpm, frequencia,
            video_3s, thruplays,
            adicoes_carrinho, checkout_iniciado, visualizacoes_landing
        )
        SELECT
            data_inicio, campanha,
            COALESCE(impressoes, 0), COALESCE(alcance, 0),
            COALESCE(cliques_link, 0), COALESCE(cliques_todos, 0),
            COALESCE(investimento, 0), COALESCE(compras, 0), COALESCE(valor_compras, 0),
            roas_compras, cpm, frequencia,
            COALESCE(video_3s, 0), COALESCE(thruplays, 0),
            COALESCE(adicoes_carrinho, 0), COALESCE(checkout_iniciado, 0),
            COALESCE(visualizacoes_landing, 0)
        FROM campanhas
        WHERE data_inicio < '2026-05-14'
    """)
    n2 = conn.execute("SELECT COUNT(*) FROM source_of_meta_ads_diario").fetchone()[0]
    logger.info("source_of_meta_ads_diario: %d linhas (ads_diario: %d + histórico: %d)", n2, n1, n2 - n1)
    conn.commit()

    # 3. Migra Source OF · Google Ads
    logger.info("Migrando source_of_gads_diario...")
    conn.execute("""
        INSERT OR IGNORE INTO source_of_gads_diario (
            data, campanha, campaign_id, tipo_campanha, status,
            impressoes, cliques, investimento, conversoes, valor_conversao, ctr, cpc_medio
        )
        SELECT
            data, campanha, campaign_id, tipo_campanha, status,
            COALESCE(impressoes, 0), COALESCE(cliques, 0), COALESCE(investimento, 0),
            COALESCE(conversoes, 0), COALESCE(valor_conversao, 0), ctr, cpc_medio
        FROM gads_campanhas_diario
        WHERE customer_id = '4311260590'
    """)
    n3 = conn.execute("SELECT COUNT(*) FROM source_of_gads_diario").fetchone()[0]
    logger.info("source_of_gads_diario: %d linhas", n3)
    conn.commit()

    # 4. Migra Source OF · Shopify
    logger.info("Migrando source_of_shopify_diario...")
    conn.execute("""
        INSERT OR IGNORE INTO source_of_shopify_diario (
            data, receita, pedidos, novos_clientes, recompradores,
            ticket_medio, total_desconto, pedidos_acima_300, atualizado_em
        )
        SELECT
            data, receita, pedidos, novos_clientes, recompradores,
            ticket_medio, total_desconto, pedidos_acima_300, atualizado_em
        FROM shopify_receita_diaria
    """)
    n4 = conn.execute("SELECT COUNT(*) FROM source_of_shopify_diario").fetchone()[0]
    logger.info("source_of_shopify_diario: %d linhas", n4)
    conn.commit()

    # 5. Migra POD Kombucha · Google Ads
    logger.info("Migrando pod_kombucha_gads_diario...")
    conn.execute("""
        INSERT OR IGNORE INTO pod_kombucha_gads_diario (
            data, campanha, campaign_id, tipo_campanha,
            impressoes, cliques, investimento, conversoes, valor_conversao
        )
        SELECT
            data_inicio, campanha, campaign_id, tipo_campanha,
            COALESCE(impressoes, 0), COALESCE(cliques, 0), COALESCE(investimento, 0),
            COALESCE(conversoes, 0), COALESCE(valor_conversao, 0)
        FROM pod_gads_diario
    """)
    n5 = conn.execute("SELECT COUNT(*) FROM pod_kombucha_gads_diario").fetchone()[0]
    logger.info("pod_kombucha_gads_diario: %d linhas", n5)
    conn.commit()

    # 6. Recria views
    logger.info("Recriando views...")
    conn.execute("DROP VIEW IF EXISTS v_performance")
    conn.execute("DROP VIEW IF EXISTS campanhas_unificada")
    conn.execute(VIEW_PERFORMANCE)
    conn.execute(VIEW_UNIFICADA)
    conn.commit()
    logger.info("Views recriadas: v_performance, campanhas_unificada")


def validar(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    print("\n" + "="*60)
    print("  VALIDAÇÃO PÓS-MIGRAÇÃO")
    print("="*60)

    tabelas = [
        ("source_of_meta_ads_diario", "data",  "investimento"),
        ("source_of_gads_diario",     "data",  "investimento"),
        ("source_of_shopify_diario",  "data",  "receita"),
        ("pod_kombucha_gads_diario",  "data",  "investimento"),
    ]
    for t, col, val_col in tabelas:
        r = conn.execute(f"""
            SELECT COUNT(*) as n, MIN({col}) as inicio, MAX({col}) as fim,
                   ROUND(SUM({val_col}),2) as valor
            FROM \"{t}\"
        """).fetchone()
        label = "Receita" if val_col == "receita" else "Investimento"
        print(f"\n  {t}")
        print(f"    Linhas: {r['n']} | {r['inicio']} → {r['fim']}")
        if r['valor'] is not None:
            print(f"    {label} total: R$ {r['valor']:,.2f}")

    print("\n  v_performance (por cliente + plataforma):")
    rows = conn.execute("""
        SELECT cliente_nome, plataforma,
               COUNT(*) as linhas,
               MIN(data) as inicio, MAX(data) as fim,
               ROUND(SUM(investimento),2) as invest
        FROM v_performance
        GROUP BY cliente_key, plataforma
        ORDER BY cliente_key, plataforma
    """).fetchall()
    for r in rows:
        print(f"    {r['cliente_nome']:<15} | {r['plataforma']:<12} | {r['linhas']:>6} linhas | {r['inicio']} → {r['fim']} | R$ {r['invest']:>12,.2f}")

    print("\n" + "="*60 + "\n")


if __name__ == "__main__":
    logger.info("Iniciando migração...")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    migrar(conn)
    validar(conn)
    conn.close()
    logger.info("Migração concluída.")
