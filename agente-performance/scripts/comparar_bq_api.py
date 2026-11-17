"""
scripts/comparar_bq_api.py

Compara o BigQuery com as APIs de origem, por cliente, fonte e dia, e grava o resultado em
correlacao.comparacao_bq_api. Mede a métrica de aceite "divergência entre o banco e a API
abaixo de 2%" do card BigQuery Guará.

  Meta    → investimento (spend da conta) x correlacao.fato_performance_diaria (canal meta)
  Google  → investimento (cost_micros)    x correlacao.fato_performance_diaria (canal google)
  Shopify → pedidos pagos criados no dia  x ingestao.ing_shopify_resumo_diario

Só lê das APIs (não grava nas tabelas de carga). Compara dias fechados: de D-N a D-1.
Rodar depois da renovação dos tokens da Shopify (eles vencem a cada 24h).

Uso:
  python3 scripts/comparar_bq_api.py              # últimos 7 dias fechados
  python3 scripts/comparar_bq_api.py --dias 30
  python3 scripts/comparar_bq_api.py --cliente pod_kombucha --sem-gravar
"""
import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("comparar_bq_api")

PROJECT = os.environ.get("BQ_PROJECT", "guara-db")
TABELA = f"{PROJECT}.correlacao.comparacao_bq_api"
LIMITE_PCT = 2.0
META_API = "https://graph.facebook.com/v21.0"
SHOPIFY_API = "2024-01"
# Loja e token de cada cliente (Source Of usa as variáveis sem sufixo, como no resto do código).
SHOPIFY_PADRAO = {"source_of": ("SHOPIFY_STORE", "SHOPIFY_TOKEN")}


def _dias(inicio: date, fim: date) -> list[str]:
    return [(inicio + timedelta(d)).isoformat() for d in range((fim - inicio).days + 1)]


# ── APIs ──────────────────────────────────────────────────────────────────────

def meta_api(cfg: dict, inicio: date, fim: date) -> dict[str, float]:
    token = os.environ.get(cfg.get("meta_token_env", "META_ACCESS_TOKEN"), "")
    if not token:
        raise RuntimeError(f"{cfg.get('meta_token_env', 'META_ACCESS_TOKEN')} não está no .env")
    contas = [c for c in (cfg.get("meta_account_id"), cfg.get("meta_account_id2")) if c]
    total: dict[str, float] = {}
    for conta in contas:
        url = f"{META_API}/{conta}/insights"
        params = {"fields": "spend", "level": "account", "time_increment": 1,
                  "time_range": json.dumps({"since": inicio.isoformat(), "until": fim.isoformat()}),
                  "limit": 500, "access_token": token}
        while url:
            r = requests.get(url, params=params, timeout=60).json()
            if "error" in r:
                raise RuntimeError(f"Meta {conta}: {r['error'].get('message', '')[:200]}")
            for linha in r.get("data", []):
                total[linha["date_start"]] = total.get(linha["date_start"], 0.0) + float(linha.get("spend", 0))
            url, params = r.get("paging", {}).get("next"), None
    return total


def google_api(cfg: dict, inicio: date, fim: date) -> dict[str, float]:
    from google_ads.loader import get_client
    customer_id = cfg.get("gads_customer_id")
    if not customer_id:
        raise RuntimeError("sem gads_customer_id no clientes.json")
    servico = get_client().get_service("GoogleAdsService")
    consulta = ("SELECT segments.date, metrics.cost_micros FROM customer "
                f"WHERE segments.date BETWEEN '{inicio}' AND '{fim}'")
    total: dict[str, float] = {}
    for lote in servico.search_stream(customer_id=str(customer_id), query=consulta):
        for linha in lote.results:
            total[linha.segments.date] = total.get(linha.segments.date, 0.0) + linha.metrics.cost_micros / 1e6
    return total


def shopify_api(cliente: str, cfg: dict, inicio: date, fim: date) -> dict[str, float]:
    var_loja, var_token = SHOPIFY_PADRAO.get(cliente, (None, cfg.get("token_env")))
    loja = os.environ.get(var_loja, "") if var_loja else cfg.get("store", "")
    token = os.environ.get(var_token or "", "")
    if not loja or not token:
        raise RuntimeError("loja ou token da Shopify não configurados")
    loja = loja if loja.endswith(".myshopify.com") else f"{loja}.myshopify.com"
    total: dict[str, float] = {}
    for dia in _dias(inicio, fim):
        r = requests.get(
            f"https://{loja}/admin/api/{SHOPIFY_API}/orders/count.json",
            headers={"X-Shopify-Access-Token": token},
            params={"status": "any", "financial_status": "paid",
                    "created_at_min": f"{dia}T00:00:00-03:00", "created_at_max": f"{dia}T23:59:59-03:00"},
            timeout=30)
        if r.status_code == 401:
            raise RuntimeError("token da Shopify vencido (rode a renovação antes)")
        r.raise_for_status()
        total[dia] = float(r.json().get("count", 0))
    return total


# ── BigQuery ──────────────────────────────────────────────────────────────────

def banco(bq, inicio: date, fim: date) -> dict[tuple, float]:
    from google.cloud import bigquery
    params = [bigquery.ScalarQueryParameter("ini", "DATE", inicio),
              bigquery.ScalarQueryParameter("fim", "DATE", fim)]
    consulta = f"""
      SELECT cliente_id AS cliente, canal AS fonte, CAST(data AS STRING) AS dia,
             CAST(SUM(investimento) AS FLOAT64) AS valor
      FROM `{PROJECT}.correlacao.fato_performance_diaria`
      WHERE canal IN ('meta', 'google') AND data BETWEEN @ini AND @fim
      GROUP BY 1, 2, 3
      UNION ALL
      SELECT cliente, 'shopify', CAST(data AS STRING), CAST(SUM(pedidos) AS FLOAT64)
      FROM `{PROJECT}.ingestao.ing_shopify_resumo_diario`
      WHERE data BETWEEN @ini AND @fim
      GROUP BY 1, 2, 3"""
    linhas = bq.query(consulta, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    return {(l.cliente, l.fonte, l.dia): l.valor for l in linhas}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=7)
    ap.add_argument("--cliente")
    ap.add_argument("--sem-gravar", action="store_true", help="Só mostra, não grava no BigQuery")
    args = ap.parse_args()

    from google.cloud import bigquery
    bq = bigquery.Client(project=PROJECT)
    fim = date.today() - timedelta(days=1)
    inicio = fim - timedelta(days=args.dias - 1)
    dados_bq = banco(bq, inicio, fim)

    clientes = json.loads((BASE_DIR / "clientes.json").read_text())["clientes"]
    agora = datetime.now(timezone.utc).isoformat()
    linhas, falhas = [], []
    for cliente, cfg in clientes.items():
        if args.cliente and cliente != args.cliente:
            continue
        plat = cfg.get("plataformas", {})
        fontes = [("meta", "meta_ads", "investimento", lambda c=plat.get("meta_ads", {}): meta_api(c, inicio, fim)),
                  ("google", "google_ads", "investimento", lambda c=plat.get("google_ads", {}): google_api(c, inicio, fim)),
                  ("shopify", "shopify", "pedidos_pagos", lambda c=plat.get("shopify", {}), k=cliente: shopify_api(k, c, inicio, fim))]
        for fonte, chave, metrica, buscar in fontes:
            if not plat.get(chave, {}).get("ativo"):
                continue
            try:
                api = buscar()
            except Exception as exc:
                logger.error("❌ %s/%s: %s", cliente, fonte, exc)
                falhas.append(f"{cliente}/{fonte}")
                continue
            for dia in _dias(inicio, fim):
                v_api = round(api.get(dia, 0.0), 2)
                v_bq = round(dados_bq.get((cliente, fonte, dia), 0.0), 2)
                dif = 0.0 if v_api == v_bq else (abs(v_bq - v_api) / v_api * 100 if v_api else 100.0)
                linhas.append({"executado_em": agora, "cliente": cliente, "fonte": fonte, "metrica": metrica,
                               "dia": dia, "valor_bq": v_bq, "valor_api": v_api, "diferenca_pct": round(dif, 2)})

    print(f"\nComparação BigQuery x API de {inicio:%d/%m} a {fim:%d/%m} (limite {LIMITE_PCT:g}%)")
    print(f"{'cliente/fonte':<24}{'métrica':<15}{'BigQuery':>14}{'API':>14}{'dif.':>8}  dias acima")
    pares = sorted({(l["cliente"], l["fonte"], l["metrica"]) for l in linhas})
    for cliente, fonte, metrica in pares:
        ls = [l for l in linhas if (l["cliente"], l["fonte"]) == (cliente, fonte)]
        soma_bq, soma_api = sum(l["valor_bq"] for l in ls), sum(l["valor_api"] for l in ls)
        dif = abs(soma_bq - soma_api) / soma_api * 100 if soma_api else (0.0 if not soma_bq else 100.0)
        acima = [l["dia"][8:10] + "/" + l["dia"][5:7] for l in ls if l["diferenca_pct"] > LIMITE_PCT]
        print(f"{cliente + '/' + fonte:<24}{metrica:<15}{soma_bq:>14,.2f}{soma_api:>14,.2f}{dif:>7.2f}%  "
              f"{len(acima)}/{len(ls)} {' '.join(acima)}")
    if falhas:
        print("Sem comparação (erro na API):", ", ".join(falhas))

    if linhas and not args.sem_gravar:
        bq.create_table(bigquery.Table(TABELA, schema=[
            bigquery.SchemaField("executado_em", "TIMESTAMP"), bigquery.SchemaField("cliente", "STRING"),
            bigquery.SchemaField("fonte", "STRING"), bigquery.SchemaField("metrica", "STRING"),
            bigquery.SchemaField("dia", "DATE"), bigquery.SchemaField("valor_bq", "FLOAT"),
            bigquery.SchemaField("valor_api", "FLOAT"), bigquery.SchemaField("diferenca_pct", "FLOAT")]),
            exists_ok=True)
        erros = bq.insert_rows_json(TABELA, linhas)
        if erros:
            raise RuntimeError(f"falha ao gravar {TABELA}: {erros[:1]}")
        logger.info("✅ %d linhas gravadas em %s", len(linhas), TABELA)
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
