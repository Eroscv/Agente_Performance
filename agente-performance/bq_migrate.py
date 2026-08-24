"""
Migração histórica SQLite → BigQuery
Roda uma vez. Idempotente (usa WRITE_TRUNCATE por tabela/cliente).
"""
import os, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
from google.cloud import bigquery

BASE_DIR   = Path(__file__).parent
CREDS      = BASE_DIR / "credentials" / "bq-service-account.json"
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(CREDS))

client  = bigquery.Client()
PROJECT = client.project
DATASET = "guara_performance"

def _db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

def bq_insert(table_id: str, rows: list[dict], desc: str):
    if not rows:
        print(f"  ⚠️  {desc} — sem dados")
        return
    table = client.get_table(f"{PROJECT}.{DATASET}.{table_id}")
    # Batch de 500 (limite recomendado)
    total_errors = []
    for i in range(0, len(rows), 500):
        batch = rows[i:i+500]
        errors = client.insert_rows_json(table, batch)
        total_errors.extend(errors)
    if total_errors:
        print(f"  ❌ {desc} — {len(total_errors)} erros: {total_errors[:2]}")
    else:
        print(f"  ✅ {desc} — {len(rows)} linhas inseridas")

# ─────────────────────────────────────────
# META ADS — Source OF
# ─────────────────────────────────────────
def migrar_meta_source_of():
    db = _db(BASE_DIR / "data/performance/source_of/meta_ads.db")
    rows_raw = db.execute("SELECT * FROM source_of_meta_ads_diario ORDER BY data").fetchall()
    agora = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in rows_raw:
        inv = r["investimento"] or 0
        comp = r["compras"] or 0
        rec = r["valor_compras"] or 0
        rows.append({
            "cliente":             "source_of",
            "plataforma":          "meta",
            "data":                r["data"],
            "campanha":            r["campanha"],
            "campaign_id":         r["campaign_id"],
            "adset_name":          r["adset_name"],
            "adset_id":            r["adset_id"],
            "ad_name":             r["ad_name"],
            "ad_id":               r["ad_id"],
            "objetivo":            r["objetivo"],
            "status":              r["status_criativo"],
            "investimento":        str(round(inv, 2)),
            "compras":             int(comp),
            "receita":             str(round(rec, 2)),
            "impressoes":          r["impressoes"],
            "alcance":             r["alcance"],
            "cliques_link":        r["cliques_link"],
            "cliques_todos":       r["cliques_todos"],
            "roas":                r["roas_compras"],
            "cpa":                 r["cpa_meta"],
            "cpm":                 r["cpm"],
            "frequencia":          r["frequencia"],
            "video_3s":            r["video_3s"],
            "video_50pct":         r["video_50pct"],
            "thruplays":           r["thruplays"],
            "adicoes_carrinho":    r["adicoes_carrinho"],
            "checkout_iniciado":   r["checkout_iniciado"],
            "visualizacoes_landing": r["visualizacoes_landing"],
            "qualidade_ranking":   r["qualidade_ranking"],
            "engagement_ranking":  r["engagement_ranking"],
            "conversion_ranking":  r["conversion_ranking"],
            "criado_em":           agora,
        })
    bq_insert("ads_diario", rows, "Meta Ads Source OF")
    db.close()

# ─────────────────────────────────────────
# META ADS — POD Kombucha
# ─────────────────────────────────────────
def migrar_meta_pod():
    db = _db(BASE_DIR / "data/performance/pod_kombucha/meta_ads.db")
    rows_raw = db.execute("SELECT * FROM pod_kombucha_meta_ads_diario ORDER BY data").fetchall()
    agora = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in rows_raw:
        inv = r["investimento"] or 0
        comp = r["compras"] or 0
        rec = r["valor_compras"] or 0
        rows.append({
            "cliente":             "pod_kombucha",
            "plataforma":          "meta",
            "data":                r["data"],
            "campanha":            r["campanha"],
            "campaign_id":         r["campaign_id"],
            "adset_name":          r["adset_name"],
            "adset_id":            r["adset_id"],
            "ad_name":             r["ad_name"],
            "ad_id":               r["ad_id"],
            "objetivo":            r["objetivo"],
            "status":              r["status_criativo"],
            "investimento":        str(round(inv, 2)),
            "compras":             int(comp),
            "receita":             str(round(rec, 2)),
            "impressoes":          r["impressoes"],
            "alcance":             r["alcance"],
            "cliques_link":        r["cliques_link"],
            "cliques_todos":       r["cliques_todos"],
            "roas":                r["roas_compras"],
            "cpa":                 r["cpa_meta"],
            "cpm":                 r["cpm"],
            "frequencia":          r["frequencia"],
            "video_3s":            r["video_3s"],
            "video_50pct":         r["video_50pct"],
            "thruplays":           r["thruplays"],
            "adicoes_carrinho":    r["adicoes_carrinho"],
            "checkout_iniciado":   r["checkout_iniciado"],
            "visualizacoes_landing": r["visualizacoes_landing"],
            "qualidade_ranking":   r["qualidade_ranking"],
            "engagement_ranking":  r["engagement_ranking"],
            "conversion_ranking":  r["conversion_ranking"],
            "criado_em":           agora,
        })
    bq_insert("ads_diario", rows, "Meta Ads POD Kombucha")
    db.close()

# ─────────────────────────────────────────
# GOOGLE ADS — Source OF
# ─────────────────────────────────────────
def migrar_google_source_of():
    db = _db(BASE_DIR / "data/performance/source_of/google_ads.db")
    rows_raw = db.execute("SELECT * FROM source_of_gads_diario ORDER BY data").fetchall()
    agora = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in rows_raw:
        inv = r["investimento"] or 0
        comp = r["conversoes"] or 0
        rec = r["valor_conversao"] or 0
        rows.append({
            "cliente":      "source_of",
            "plataforma":   "google",
            "data":         r["data"],
            "campanha":     r["campanha"],
            "campaign_id":  r["campaign_id"],
            "status":       r["status"] if "status" in r.keys() else None,
            "investimento": str(round(inv, 2)),
            "compras":      int(comp),
            "receita":      str(round(rec, 2)),
            "impressoes":   r["impressoes"],
            "cliques_link": r["cliques"],
            "roas":         round(rec / inv, 4) if inv > 0 else 0.0,
            "cpa":          round(inv / comp, 2) if comp > 0 else 0.0,
            "cpm":          round((inv / r["impressoes"] * 1000), 2) if r["impressoes"] else None,
            "criado_em":    agora,
        })
    bq_insert("ads_diario", rows, "Google Ads Source OF")
    db.close()

# ─────────────────────────────────────────
# GOOGLE ADS — POD Kombucha
# ─────────────────────────────────────────
def migrar_google_pod():
    db = _db(BASE_DIR / "data/performance/pod_kombucha/google_ads.db")
    rows_raw = db.execute("SELECT * FROM pod_kombucha_gads_diario ORDER BY data").fetchall()
    agora = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in rows_raw:
        inv = r["investimento"] or 0
        comp = r["conversoes"] or 0
        rec = r["valor_conversao"] or 0
        rows.append({
            "cliente":      "pod_kombucha",
            "plataforma":   "google",
            "data":         r["data"],
            "campanha":     r["campanha"],
            "campaign_id":  r["campaign_id"],
            "investimento": str(round(inv, 2)),
            "compras":      int(comp),
            "receita":      str(round(rec, 2)),
            "impressoes":   r["impressoes"],
            "cliques_link": r["cliques"],
            "roas":         round(rec / inv, 4) if inv > 0 else 0.0,
            "cpa":          round(inv / comp, 2) if comp > 0 else 0.0,
            "cpm":          round((inv / r["impressoes"] * 1000), 2) if r["impressoes"] else None,
            "criado_em":    agora,
        })
    bq_insert("ads_diario", rows, "Google Ads POD Kombucha")
    db.close()

# ─────────────────────────────────────────
# SHOPIFY — Source OF
# ─────────────────────────────────────────
def migrar_shopify_source_of():
    db = _db(BASE_DIR / "data/performance/source_of/shopify_receita.db")
    rows_raw = db.execute("SELECT * FROM shopify_receita_diaria ORDER BY data").fetchall()
    agora = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in rows_raw:
        rows.append({
            "cliente":          "source_of",
            "data":             r["data"],
            "receita":          str(round(r["receita"] or 0, 2)),
            "pedidos":          int(r["pedidos"] or 0),
            "novos_clientes":   int(r["novos_clientes"] or 0),
            "recompradores":    int(r["recompradores"] or 0),
            "ticket_medio":     str(round(r["ticket_medio"] or 0, 2)),
            "total_desconto":   str(round(r["total_desconto"] or 0, 2)),
            "pedidos_acima_300": int(r["pedidos_acima_300"] or 0),
            "criado_em":        agora,
        })
    bq_insert("shopify_diario", rows, "Shopify Source OF")
    db.close()

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  MIGRAÇÃO SQLite → BigQuery")
    print(f"  Projeto: {PROJECT} | Dataset: {DATASET}")
    print("=" * 55)
    t0 = time.monotonic()

    migrar_meta_source_of()
    migrar_meta_pod()
    migrar_google_source_of()
    migrar_google_pod()
    migrar_shopify_source_of()

    elapsed = time.monotonic() - t0
    print("=" * 55)
    print(f"  Concluído em {elapsed:.1f}s")
    print("=" * 55)
