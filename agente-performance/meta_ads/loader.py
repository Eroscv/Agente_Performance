"""
meta_ads/loader.py

Puxa dados de campanhas Meta Ads via API e carrega no SQLite.
Substitui loader_sheets.py para todos os clientes Meta Ads.

Uso programático:
    from meta_ads.loader import carregar_meta
    linhas, periodo = carregar_meta(account_id="act_1059884611898142", dias=30)

Uso CLI:
    python -m meta_ads.loader --account-id act_1059884611898142 --dias 30
    python -m meta_ads.loader --account-id act_1059884611898142 --dry-run
"""

import argparse
import logging
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
import os
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BASE_DIR  = Path(__file__).parent.parent
DB_PATH   = BASE_DIR / "data" / "campanhas.db"
API_BASE  = "https://graph.facebook.com/v20.0"

# DDL template — tabela por cliente
_CREATE_TABLE_TPL = """
CREATE TABLE IF NOT EXISTS {tabela} (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    data                TEXT NOT NULL,
    campanha            TEXT,
    campaign_id         TEXT,
    adset_name          TEXT,
    adset_id            TEXT,
    ad_name             TEXT,
    ad_id               TEXT,
    objetivo            TEXT,
    status_criativo     TEXT,
    impressoes          REAL DEFAULT 0,
    alcance             REAL DEFAULT 0,
    cliques_link        REAL DEFAULT 0,
    cliques_todos       REAL DEFAULT 0,
    investimento        REAL DEFAULT 0,
    compras             REAL DEFAULT 0,
    valor_compras       REAL DEFAULT 0,
    cpa_meta            REAL,
    roas_compras        REAL,
    cpm                 REAL,
    frequencia          REAL,
    video_3s            REAL DEFAULT 0,
    video_50pct         REAL DEFAULT 0,
    thruplays           REAL DEFAULT 0,
    adicoes_carrinho    REAL DEFAULT 0,
    checkout_iniciado   REAL DEFAULT 0,
    visualizacoes_landing REAL DEFAULT 0,
    qualidade_ranking   TEXT,
    engagement_ranking  TEXT,
    conversion_ranking  TEXT,
    UNIQUE(data, campanha, ad_id)
)
"""

# Campos que vamos pedir à API
FIELDS = ",".join([
    "campaign_name",
    "campaign_id",
    "adset_name",
    "adset_id",
    "ad_name",
    "ad_id",
    "date_start",
    "impressions",
    "reach",
    "clicks",
    "spend",
    "cpm",
    "frequency",
    "actions",
    "action_values",
    "video_thruplay_watched_actions",
    "video_play_actions",
    "objective",
    "quality_ranking",
    "engagement_rate_ranking",
    "conversion_rate_ranking",
])


def _get_token(token_env: str = "META_ACCESS_TOKEN") -> str:
    token = os.environ.get(token_env, "")
    if not token:
        raise EnvironmentError(f"{token_env} não definido no .env")
    return token


def _parse_action(actions: list, action_type: str) -> float:
    """Extrai valor de uma action pelo tipo."""
    if not actions:
        return 0.0
    for a in actions:
        if a.get("action_type") == action_type:
            return float(a.get("value", 0))
    return 0.0


def _parse_action_value(action_values: list, action_type: str) -> float:
    """Extrai valor monetário de action_values pelo tipo."""
    if not action_values:
        return 0.0
    for a in action_values:
        if a.get("action_type") == action_type:
            return float(a.get("value", 0))
    return 0.0


def _parse_video(video_actions: list) -> float:
    if not video_actions:
        return 0.0
    return float(video_actions[0].get("value", 0)) if video_actions else 0.0


def _normalizar(row: dict) -> dict:
    """Normaliza um registro da API para o schema interno."""
    actions       = row.get("actions", [])
    action_values = row.get("action_values", [])

    compras       = _parse_action(actions, "purchase")
    valor_compras = _parse_action_value(action_values, "purchase")
    investimento  = float(row.get("spend", 0) or 0)
    impressoes    = float(row.get("impressions", 0) or 0)
    cliques_link  = float(row.get("clicks", 0) or 0)

    roas = round(valor_compras / investimento, 4) if investimento > 0 else None
    cpa  = round(investimento / compras, 2) if compras > 0 else None

    return {
        "data":               row.get("date_start"),
        "campanha":           row.get("campaign_name"),
        "campaign_id":        row.get("campaign_id"),
        "adset_name":         row.get("adset_name"),
        "adset_id":           row.get("adset_id"),
        "ad_name":            row.get("ad_name"),
        "ad_id":              row.get("ad_id"),
        "objetivo":           row.get("objective"),
        "status_criativo":    None,
        "impressoes":         impressoes,
        "alcance":            float(row.get("reach", 0) or 0),
        "cliques_link":       cliques_link,
        "cliques_todos":      float(row.get("unique_clicks", 0) or 0),
        "investimento":       investimento,
        "compras":            compras,
        "valor_compras":      valor_compras,
        "cpa_meta":           cpa,
        "roas_compras":       roas,
        "cpm":                float(row.get("cpm", 0) or 0),
        "frequencia":         float(row.get("frequency", 0) or 0),
        "video_3s":           _parse_video(row.get("video_play_actions")),
        "video_50pct":        _parse_video(row.get("video_p50_watched_actions")),
        "thruplays":          _parse_video(row.get("video_thruplay_watched_actions")),
        "adicoes_carrinho":   _parse_action(actions, "add_to_cart"),
        "checkout_iniciado":  _parse_action(actions, "initiate_checkout"),
        "visualizacoes_landing": _parse_action(actions, "landing_page_view"),
        # Novas métricas de qualidade de ranking (Melhoria 3)
        "qualidade_ranking":  row.get("quality_ranking"),
        "engagement_ranking": row.get("engagement_rate_ranking"),
        "conversion_ranking": row.get("conversion_rate_ranking"),
    }


def buscar_insights_periodo(account_id: str, date_start: date, date_end: date,
                            token_env: str = "META_ACCESS_TOKEN") -> list[dict]:
    """Busca insights para um período específico (máx 30 dias recomendado)."""
    token = _get_token(token_env)

    logger.info("[%s] Buscando %s → %s", account_id, date_start, date_end)

    params = {
        "access_token": token,
        "level":        "ad",
        "fields":       FIELDS,
        "time_range":   f'{{"since":"{date_start}","until":"{date_end}"}}',
        "time_increment": 1,
        "limit":        500,
        "filtering":    '[{"field":"spend","operator":"GREATER_THAN","value":"0"}]',
    }

    url  = f"{API_BASE}/{account_id}/insights"
    rows = []
    page = 0

    while url:
        resp = requests.get(url, params=params if page == 0 else {}, timeout=60)
        if resp.status_code == 429:
            logger.warning("Rate limit — aguardando 60s")
            time.sleep(60)
            continue

        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Meta API error: {data['error'].get('message')}")

        batch = data.get("data", [])
        rows.extend([_normalizar(r) for r in batch])
        page += 1

        paging   = data.get("paging", {})
        next_url = paging.get("next")
        url      = next_url if next_url else None
        params   = {}

    return rows


def buscar_insights(account_id: str, dias: int = 30,
                    token_env: str = "META_ACCESS_TOKEN") -> list[dict]:
    """Busca insights de anúncios via API em lotes de 14 dias."""
    _get_token(token_env)  # valida token antes de iterar
    date_end   = date.today() - timedelta(days=1)
    date_start = date_end - timedelta(days=dias - 1)

    logger.info("[%s] Buscando %d dias (%s → %s) em lotes", account_id, dias, date_start, date_end)

    LOTE = 1  # 1 dia por vez — ranges maiores causam HTTP 500 intermitente na Meta API
    all_rows = []
    cursor   = date_start

    while cursor <= date_end:
        lote_fim = min(cursor + timedelta(days=LOTE - 1), date_end)
        # Retry por lote: até 3 tentativas com backoff
        for tentativa in range(1, 4):
            try:
                rows = buscar_insights_periodo(account_id, cursor, lote_fim, token_env=token_env)
                all_rows.extend(rows)
                logger.info("[%s] Dia %s: %d linhas", account_id, cursor, len(rows))
                break
            except Exception as exc:
                logger.warning("[%s] Dia %s tentativa %d/3 falhou: %s", account_id, cursor, tentativa, exc)
                if tentativa < 3:
                    time.sleep(5 * tentativa)
                else:
                    logger.error("[%s] Dia %s abortado após 3 tentativas", account_id, cursor)
        cursor = lote_fim + timedelta(days=1)
        if cursor <= date_end:
            time.sleep(0.5)  # pausa leve entre dias

    logger.info("[%s] Total: %d linhas", account_id, len(all_rows))
    return all_rows


def salvar(rows: list[dict], append: bool = False,
           tabela: str = "source_of_meta_ads_diario",
           db_path: "Path | None" = None) -> int:
    """Salva dados no SQLite na tabela especificada."""
    if not rows:
        logger.warning("Nenhuma linha para salvar")
        return 0

    _db = Path(db_path) if db_path else DB_PATH
    _db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_TABLE_TPL.format(tabela=tabela))
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabela}_data ON {tabela}(data)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabela}_camp ON {tabela}(campanha)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{tabela}_data_camp ON {tabela}(data, campanha)")

    if not append:
        # Deleta apenas o período que está sendo recarregado
        datas = sorted({r["data"] for r in rows if r.get("data")})
        if datas:
            conn.execute(
                f"DELETE FROM {tabela} WHERE data BETWEEN ? AND ?",
                (datas[0], datas[-1])
            )
            logger.info("[%s] Período %s → %s limpo para recarga", tabela, datas[0], datas[-1])

    cols = [
        "data", "campanha", "campaign_id", "adset_name", "adset_id",
        "ad_name", "ad_id", "objetivo", "status_criativo",
        "impressoes", "alcance", "cliques_link", "cliques_todos",
        "investimento", "compras", "valor_compras", "cpa_meta", "roas_compras",
        "cpm", "frequencia", "video_3s", "video_50pct", "thruplays",
        "adicoes_carrinho", "checkout_iniciado", "visualizacoes_landing",
        "qualidade_ranking", "engagement_ranking", "conversion_ranking",
    ]
    sql = f"""
        INSERT OR REPLACE INTO {tabela} ({', '.join(cols)})
        VALUES ({', '.join(['?'] * len(cols))})
    """
    for r in rows:
        conn.execute(sql, [r.get(c) for c in cols])

    conn.commit()
    conn.close()
    logger.info("%d linhas salvas em %s", len(rows), tabela)
    return len(rows)


def resumo_banco(tabela: str = "source_of_meta_ads_diario", db_path: "Path | None" = None) -> dict:
    _db = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(_db))
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(f"""
            SELECT COUNT(*) AS n,
                   MIN(data) AS inicio, MAX(data) AS fim,
                   COUNT(DISTINCT campanha) AS campanhas,
                   ROUND(SUM(investimento), 2) AS investimento_total,
                   ROUND(SUM(valor_compras), 2) AS receita_total
            FROM {tabela}
        """).fetchone()
        return dict(r)
    except Exception:
        return {}
    finally:
        conn.close()


def carregar_meta(account_id: str, dias: int = 30, append: bool = False,
                  tabela: str = "source_of_meta_ads_diario",
                  token_env: str = "META_ACCESS_TOKEN",
                  db_path: "Path | None" = None):
    """
    Wrapper programático — busca via API e salva no SQLite.

    Args:
        account_id: ID da conta Meta Ads (com ou sem 'act_')
        dias: quantos dias puxar (padrão 30)
        append: se True, não limpa o período antes de inserir
        tabela: nome da tabela destino
        token_env: variável de ambiente com o token (padrão META_ACCESS_TOKEN)
        db_path: caminho do arquivo .db destino (padrão: data/campanhas.db legado)

    Returns:
        (linhas_inseridas: int, periodo: str)
    """
    if not account_id.startswith("act_"):
        account_id = f"act_{account_id}"

    rows   = buscar_insights(account_id=account_id, dias=dias, token_env=token_env)
    linhas = salvar(rows, append=append, tabela=tabela, db_path=db_path)
    info   = resumo_banco(tabela, db_path=db_path)
    periodo = f"{info.get('inicio', '?')} → {info.get('fim', '?')}"
    return linhas, periodo


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Meta Ads API → SQLite")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--tabela", default="source_of_meta_ads_diario")
    parser.add_argument("--dias",   type=int, default=30)
    parser.add_argument("--append", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    aid = args.account_id
    if not aid.startswith("act_"):
        aid = f"act_{aid}"

    if args.dry_run:
        rows = buscar_insights(account_id=aid, dias=args.dias)
        datas = sorted({r["data"] for r in rows if r.get("data")})
        print(f"\nDRY RUN — account={aid}, dias={args.dias}")
        print(f"Linhas: {len(rows)}")
        print(f"Período: {datas[0]} → {datas[-1]}" if datas else "Sem dados")
        import sys; sys.exit(0)

    linhas, periodo = carregar_meta(
        account_id=aid, dias=args.dias, append=args.append, tabela=args.tabela
    )
    info = resumo_banco(args.tabela)
    print(f"\n{'='*55}")
    print(f"  ✅ Meta Ads API → SQLite")
    print(f"  Account ID:        {aid}")
    print(f"  Tabela:            {args.tabela}")
    print(f"  Linhas inseridas:  {linhas}")
    print(f"  Período:           {periodo}")
    print(f"  Campanhas únicas:  {info.get('campanhas')}")
    print(f"  Investimento:      R$ {info.get('investimento_total', 0):,.2f}")
    print(f"  Receita:           R$ {info.get('receita_total', 0):,.2f}")
    print(f"{'='*55}\n")
