"""Regenera dashboard-source-of/src/data.ts a partir do BigQuery (cliente source_of).

Uso:
    python3 scripts/gerar_dados_dashboard_source_of.py
"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from google.cloud import bigquery

BASE = Path(__file__).resolve().parent.parent
SAIDA = BASE / "dashboard-source-of" / "src" / "data.ts"
CREDENCIAL = BASE / "credentials" / "bq-service-account.json"
PROJETO = "guara-db"
CLIENTE = "source_of"
DESDE = "2026-08-31"

CONSULTAS = {
    "SHOPIFY": ("ShopifyDay", """
        SELECT data, receita, pedidos, novos_clientes, recompradores, ticket_medio,
               total_desconto, frete, pedidos_gt300, gross_sales,
               sessoes, sessoes_compra, taxa_conversao
        FROM `guara-db.ingestao.ing_shopify_resumo_diario`
        WHERE cliente = @cliente AND data >= @desde
        ORDER BY data"""),
    "ADS": ("AdsDay", """
        SELECT data, canal, investimento, pedidos, receita, novos_clientes
        FROM `guara-db.correlacao.fato_performance_diaria`
        WHERE cliente_id = @cliente AND data >= @desde
        ORDER BY data, canal"""),
    "CAMPANHAS_DIARIO": ("CampanhaDiaria", """
        SELECT data, plataforma, campanha,
               SUM(investimento) AS inv, SUM(compras) AS compras, SUM(receita) AS receita
        FROM `guara-db.ingestao.ing_ads_diario`
        WHERE cliente = @cliente AND data >= @desde AND entra_no_total
        GROUP BY data, plataforma, campanha
        ORDER BY data, plataforma, campanha"""),
    "CRIATIVOS_DIARIO": ("CriativoDiaria", """
        SELECT data, campanha, adset_name AS adset, ad_name AS ad,
               SUM(investimento) AS inv, SUM(compras) AS compras, SUM(receita) AS receita,
               SUM(COALESCE(cliques_todos, 0)) AS cliques
        FROM `guara-db.ingestao.ing_ads_diario`
        WHERE cliente = @cliente AND data >= @desde AND nivel = 'anuncio' AND plataforma = 'meta'
        GROUP BY data, campanha, adset_name, ad_name
        ORDER BY data, campanha, adset_name, ad_name"""),
    "PEDIDOS_ESTADO": ("EstadoDay", """
        SELECT data_brt AS data, COALESCE(estado, '—') AS estado, COUNT(*) AS pedidos,
               SUM(COALESCE(current_subtotal_price, valor_pago - frete)) AS receita
        FROM `guara-db.ingestao.ing_shopify_pedidos`
        WHERE cliente = @cliente AND data_brt >= @desde AND status = 'paid'
        GROUP BY data, estado
        ORDER BY data, estado"""),
    "PEDIDOS_SKU": ("SkuDay", """
        SELECT data_brt AS data, COALESCE(sku, '(sem SKU)') AS sku, ANY_VALUE(produto) AS produto,
               SUM(quantidade) AS unidades, COUNT(DISTINCT order_id) AS pedidos,
               SUM(receita_item) AS receita
        FROM `guara-db.ingestao.ing_shopify_pedido_itens`
        WHERE cliente = @cliente AND data_brt >= @desde
        GROUP BY data, sku
        ORDER BY data, sku"""),
    "PEDIDOS_ORIGEM": ("OrigemDay", """
        SELECT data_brt AS data, COALESCE(utm_source, '(sem utm)') AS origem, COUNT(*) AS pedidos,
               SUM(COALESCE(current_subtotal_price, valor_pago - frete)) AS receita
        FROM `guara-db.ingestao.ing_shopify_pedidos`
        WHERE cliente = @cliente AND data_brt >= @desde AND status = 'paid'
        GROUP BY data, origem
        ORDER BY data, origem"""),
    "RECOMPRA_MENSAL": ("RecompraMes", """
        SELECT FORMAT_DATE('%Y-%m', data) AS mes, SUM(pedidos) AS pedidos,
               SUM(novos_clientes) AS novos_clientes, SUM(recompradores) AS recompradores,
               SUM(receita) AS receita
        FROM `guara-db.ingestao.ing_shopify_resumo_diario`
        WHERE cliente = @cliente
        GROUP BY mes
        ORDER BY mes"""),
    "ABANDONED": ("AbandonedDay", """
        SELECT data, SUM(checkouts) AS checkouts, SUM(subtotal_total) AS subtotal,
               SUM(recuperados) AS recuperados
        FROM `guara-db.ingestao.ing_checkout_abandonado_diario`
        WHERE cliente = @cliente
        GROUP BY data
        ORDER BY data"""),
    "ABANDONED_ORIGEM": ("AbandonedOrigemDay", """
        SELECT data, utm_source_norm AS origem, SUM(checkouts) AS checkouts,
               SUM(subtotal_total) AS subtotal, SUM(recuperados) AS recuperados
        FROM `guara-db.ingestao.ing_checkout_abandonado_diario`
        WHERE cliente = @cliente
        GROUP BY data, origem
        ORDER BY data, origem"""),
}


def _valor(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    if isinstance(v, Decimal):
        return round(float(v), 2)
    if isinstance(v, float):
        return round(v, 4)
    return v


def main() -> None:
    bq = bigquery.Client.from_service_account_json(str(CREDENCIAL), project=PROJETO)
    params = [
        bigquery.ScalarQueryParameter("cliente", "STRING", CLIENTE),
        bigquery.ScalarQueryParameter("desde", "DATE", DESDE),
    ]
    cfg = bigquery.QueryJobConfig(query_parameters=params)

    partes = [
        f"// Dados extraídos do BigQuery (guara-db) em {datetime.now():%Y-%m-%d %H:%M} para cliente = {CLIENTE}.",
        "// Gerado por scripts/gerar_dados_dashboard_source_of.py. Não edite à mão.",
        "import type {",
        "  AbandonedDay,",
        "  AbandonedOrigemDay,",
        "  AdsDay,",
        "  CampanhaDiaria,",
        "  CriativoDiaria,",
        "  EstadoDay,",
        "  OrigemDay,",
        "  RecompraMes,",
        "  SkuDay,",
        "  LastSync,",
        "  ShopifyDay,",
        '} from "./types";',
        "",
    ]
    for nome, (tipo, sql) in CONSULTAS.items():
        linhas = [{k: _valor(v) for k, v in r.items()} for r in bq.query(sql, job_config=cfg).result()]
        corpo = ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in linhas)
        partes.append(f"export const {nome}: {tipo}[] = [\n{corpo}\n];\n")
        print(f"{nome}: {len(linhas)} linhas")

    sync = {
        r.table_id: int(r.last_modified.timestamp())
        for r in bq.query(
            """SELECT table_id, TIMESTAMP_MILLIS(last_modified_time) AS last_modified
               FROM `guara-db.guara_performance.__TABLES__`
               WHERE table_id IN ('pedidos_diario', 'ads_diario', 'shopify_diario')"""
        ).result()
    }
    partes.append(
        "export const LAST_SYNC: LastSync = {\n"
        + "".join(f"  {k}: {sync[k]},\n" for k in ("pedidos_diario", "ads_diario", "shopify_diario"))
        + "};\n"
    )
    SAIDA.write_text("\n".join(partes), encoding="utf-8")
    print("gravado", SAIDA)


if __name__ == "__main__":
    main()
