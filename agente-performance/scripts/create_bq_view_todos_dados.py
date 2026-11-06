"""
DEFASADO: as views em produção estão versionadas em bigquery/scripts/views/. Este gerador
não reproduz as definições atuais e rodá-lo desfaz correções já aplicadas. Não executar.
"""
"""
Cria (ou recria) a view v_todos_dados no BigQuery.

Estratégia: cada bloco SELECT é construído como dicionário
{alias: expressão}. O assembler preenche colunas ausentes com NULL.
Assim o count de colunas é sempre exato e não depende de regex.

Uso:
    GOOGLE_APPLICATION_CREDENTIALS=credentials/bq-service-account.json \
    python3 scripts/create_bq_view_todos_dados.py
"""
import os
from google.cloud import bigquery

P = os.environ.get("BQ_PROJECT", "guara-db")
D = os.environ.get("BQ_DATASET", "guara_performance")

# A view lê a silver (ingestao), não o bronze. Tabela sem silver (analise_reunioes) fica no bronze.
_SILVER = {
    "ads_diario": "ing_ads_diario", "shopify_diario": "ing_shopify_resumo_diario",
    "pedidos_diario": "ing_shopify_pedidos", "abandoned_checkout_daily": "ing_checkout_abandonado_diario",
    "abandoned_checkout_detail": "ing_checkout_abandonado_detalhe", "novos_clientes_semanal": "ing_novos_clientes_semanal",
    "meta_campaign_totals": "ing_meta_campaign_totals", "metricas_sla": "ing_metricas_sla", "tickets_alerta": "ing_tickets",
}
FONTE = {t: f"`{P}.ingestao.{s}`" for t, s in _SILVER.items()}
# dooki_pedidos_diario (legado) agora sai de ing_yampi_pedidos, agregado por dia.
FONTE["dooki_pedidos_diario"] = f"""(
  SELECT 'dooki' AS fonte, data, COUNTIF(pago) AS pedidos,
         SUM(IF(pago, receita_sem_frete, 0)) AS receita,
         SAFE_DIVIDE(SUM(IF(pago, receita_sem_frete, 0)), COUNTIF(pago)) AS ticket_medio,
         COUNT(DISTINCT IF(pago, customer_id, NULL)) AS clientes_unicos,
         MAX(atualizado_em) AS atualizado_em
  FROM `{P}.ingestao.ing_yampi_pedidos`
  GROUP BY data
)"""

# ── Lista canônica de colunas da view ───────────────────────────────────────
COLS = [
    # identificação da linha
    "origem", "cliente", "plataforma", "data",
    # anúncios
    "campanha", "campaign_id", "adset_name", "adset_id",
    "ad_name", "ad_id", "objetivo", "status", "account_id",
    # financeiro
    "investimento", "compras", "receita", "roas", "cpa",
    # alcance / entrega
    "impressoes", "alcance", "cliques_link", "cliques_todos",
    "cpm", "frequencia",
    # vídeo / funil ads
    "video_3s", "video_50pct", "thruplays",
    "adicoes_carrinho", "checkout_iniciado", "visualizacoes_landing",
    # rankings
    "qualidade_ranking", "engagement_ranking", "conversion_ranking",
    # ids de entidades
    "order_id", "customer_id", "checkout_id", "email",
    "fonte_id", "ticket_id",
    # shopify / ecomm
    "pedidos", "novos_clientes", "recompradores",
    "ticket_medio", "total_desconto", "frete",
    "gross_sales", "receita_criada", "pedidos_criados", "pedidos_gt300",
    # sessões
    "sessoes", "sessoes_com_carrinho", "sessoes_checkout",
    "sessoes_compra", "taxa_conversao", "is_bot_suspect",
    # atribuição shopify
    "pedidos_meta", "receita_meta", "novos_meta",
    "pedidos_google", "receita_google", "novos_google",
    "pedidos_outros", "receita_outros", "novos_outros",
    # semanal
    "semana_inicio", "semana_fim", "pct_novos", "pct_retencao",
    # campaign totals
    "data_inicio", "data_fim",
    # pedidos UTMs
    "data_brt", "created_at_utc",
    "utm_source", "utm_medium", "utm_campaign_utm",
    "utm_content", "utm_term", "source_name",
    "valor_pago", "desconto", "novo_cliente",
    # abandoned
    "utm_source_norm", "checkouts", "subtotal_total",
    "recuperados", "subtotal", "landing_site", "recuperado",
    # SLA
    "janela_dias", "tipo_sla", "total_sla",
    "abertos", "resolvidos", "avg_dias", "sla_dias",
    "dentro_sla", "sla_vencidos", "ic_low_pct", "ic_high_pct", "confiavel",
    # tickets
    "reuniao_titulo", "reuniao_data",
    "descricao", "trecho",
    "resolvido_em", "resolvido_por", "evidencia",
    "aberto_em", "atualizado_em",
    # reuniões
    "sentimento", "alertas", "clientes_mencionados",
    "resumo_analise", "analisado_em",
    # dooki
    "clientes_unicos",
    # controle
    "criado_em",
]

N = len(COLS)


def block(table: str, mapping: dict) -> str:
    """Gera um bloco SELECT com exatamente N colunas."""
    lines = []
    for col in COLS:
        expr = mapping.get(col, "NULL")
        lines.append(f"  {expr} AS {col}")
    assert len(lines) == N, f"{table}: {len(lines)} cols, esperado {N}"
    body = ",\n".join(lines)
    return (
        f"-- ── {table} {'─'*(52-len(table))}\n"
        f"SELECT\n{body}\nFROM {FONTE.get(table, f'`{P}.{D}.{table}`')}"
    )


def f(expr: str) -> str:
    """Wrap em CAST(...  AS FLOAT64) se não começa com NULL."""
    if expr.upper().startswith("NULL") or "FLOAT64" in expr.upper():
        return expr
    return f"CAST({expr} AS FLOAT64)"


blocks = []

# ── ads_diario ──────────────────────────────────────────────────────────────
blocks.append(block("ads_diario", {
    "origem":               "'ads_diario'",
    "cliente":              "cliente",
    "plataforma":           "plataforma",
    "data":                 "CAST(data AS STRING)",
    "campanha":             "campanha",
    "campaign_id":          "campaign_id",
    "adset_name":           "adset_name",
    "adset_id":             "adset_id",
    "ad_name":              "ad_name",
    "ad_id":                "ad_id",
    "objetivo":             "objetivo",
    "status":               "status",
    "account_id":           "account_id",
    "investimento":         "CAST(investimento AS FLOAT64)",
    "compras":              "compras",
    "receita":              "CAST(receita AS FLOAT64)",
    "roas":                 "roas",
    "cpa":                  "cpa",
    "impressoes":           "impressoes",
    "alcance":              "alcance",
    "cliques_link":         "cliques_link",
    "cliques_todos":        "cliques_todos",
    "cpm":                  "cpm",
    "frequencia":           "frequencia",
    "video_3s":             "video_3s",
    "video_50pct":          "video_50pct",
    "thruplays":            "thruplays",
    "adicoes_carrinho":     "adicoes_carrinho",
    "checkout_iniciado":    "checkout_iniciado",
    "visualizacoes_landing":"visualizacoes_landing",
    "qualidade_ranking":    "qualidade_ranking",
    "engagement_ranking":   "engagement_ranking",
    "conversion_ranking":   "conversion_ranking",
    "criado_em":            "criado_em",
}))

# ── shopify_diario ──────────────────────────────────────────────────────────
blocks.append(block("shopify_diario", {
    "origem":               "'shopify_diario'",
    "cliente":              "cliente",
    "data":                 "CAST(data AS STRING)",
    "compras":              "CAST(pedidos AS FLOAT64)",
    "receita":              "CAST(receita AS FLOAT64)",
    "impressoes":           "CAST(sessoes AS FLOAT64)",
    "pedidos":              "pedidos",
    "novos_clientes":       "novos_clientes",
    "recompradores":        "recompradores",
    "ticket_medio":         "CAST(ticket_medio AS FLOAT64)",
    "total_desconto":       "CAST(total_desconto AS FLOAT64)",
    "frete":                "CAST(frete AS FLOAT64)",
    "gross_sales":          "CAST(gross_sales AS FLOAT64)",
    "receita_criada":       "CAST(receita_criada AS FLOAT64)",
    "pedidos_criados":      "pedidos_criados",
    "pedidos_gt300":        "pedidos_gt300",
    "sessoes":              "sessoes",
    "sessoes_com_carrinho": "sessoes_com_carrinho",
    "sessoes_checkout":     "sessoes_checkout",
    "sessoes_compra":       "sessoes_compra",
    "taxa_conversao":       "taxa_conversao",
    "is_bot_suspect":       "is_bot_suspect",
    "pedidos_meta":         "pedidos_meta",
    "receita_meta":         "CAST(receita_meta AS FLOAT64)",
    "novos_meta":           "novos_meta",
    "pedidos_google":       "pedidos_google",
    "receita_google":       "CAST(receita_google AS FLOAT64)",
    "novos_google":         "novos_google",
    "pedidos_outros":       "pedidos_outros",
    "receita_outros":       "CAST(receita_outros AS FLOAT64)",
    "novos_outros":         "novos_outros",
    "criado_em":            "criado_em",
}))

# ── pedidos_diario ──────────────────────────────────────────────────────────
blocks.append(block("pedidos_diario", {
    "origem":               "'pedidos_diario'",
    "cliente":              "cliente",
    "plataforma":           "source_name",
    "data":                 "CAST(data_brt AS STRING)",
    "status":               "status",
    "receita":              "CAST(valor_pago AS FLOAT64)",
    "valor_pago":           "CAST(valor_pago AS FLOAT64)",
    "frete":                "CAST(frete AS FLOAT64)",
    "desconto":             "CAST(desconto AS FLOAT64)",
    "order_id":             "order_id",
    "customer_id":          "customer_id",
    "data_brt":             "data_brt",
    "created_at_utc":       "created_at_utc",
    "utm_source":           "utm_source",
    "utm_medium":           "utm_medium",
    "utm_campaign_utm":     "utm_campaign",
    "utm_content":          "utm_content",
    "utm_term":             "utm_term",
    "source_name":          "source_name",
    "novo_cliente":         "novo_cliente",
    "criado_em":            "criado_em",
}))

# ── abandoned_checkout_daily ────────────────────────────────────────────────
blocks.append(block("abandoned_checkout_daily", {
    "origem":               "'abandoned_checkout_daily'",
    "cliente":              "cliente",
    "plataforma":           "utm_source_norm",
    "data":                 "CAST(data AS STRING)",
    "checkouts":            "CAST(checkouts AS FLOAT64)",
    "subtotal_total":       "CAST(subtotal_total AS FLOAT64)",
    "recuperados":          "CAST(recuperados AS FLOAT64)",
    "utm_source_norm":      "utm_source_norm",
    "criado_em":            "criado_em",
}))

# ── abandoned_checkout_detail ───────────────────────────────────────────────
blocks.append(block("abandoned_checkout_detail", {
    "origem":               "'abandoned_checkout_detail'",
    "cliente":              "cliente",
    "plataforma":           "utm_source_norm",
    "data":                 "CAST(data AS STRING)",
    "status":               "status",
    "checkout_id":          "checkout_id",
    "email":                "email",
    "subtotal":             "CAST(subtotal AS FLOAT64)",
    "utm_source":           "utm_source",
    "utm_medium":           "utm_medium",
    "utm_campaign_utm":     "utm_campaign",
    "utm_source_norm":      "utm_source_norm",
    "landing_site":         "landing_site",
    "recuperado":           "CAST(recuperado AS FLOAT64)",
    "criado_em":            "criado_em",
}))

# ── novos_clientes_semanal ──────────────────────────────────────────────────
blocks.append(block("novos_clientes_semanal", {
    "origem":               "'novos_clientes_semanal'",
    "cliente":              "cliente",
    "data":                 "CAST(semana_inicio AS STRING)",
    "compras":              "CAST(pedidos AS FLOAT64)",
    "pedidos":              "pedidos",
    "novos_clientes":       "novos_clientes",
    "recompradores":        "recompradores",
    "pct_novos":            "pct_novos",
    "pct_retencao":         "pct_retencao",
    "semana_inicio":        "CAST(semana_inicio AS STRING)",
    "semana_fim":           "CAST(semana_fim AS STRING)",
    "novos_meta":           "novos_meta",
    "novos_google":         "novos_google",
    "novos_outros":         "novos_outros",
    "criado_em":            "criado_em",
}))

# ── meta_campaign_totals ────────────────────────────────────────────────────
blocks.append(block("meta_campaign_totals", {
    "origem":               "'meta_campaign_totals'",
    "cliente":              "cliente",
    "plataforma":           "'meta'",
    "data":                 "CAST(data_inicio AS STRING)",
    "campanha":             "campanha",
    "campaign_id":          "campaign_id",
    "account_id":           "account_id",
    "investimento":         "CAST(investimento AS FLOAT64)",
    "compras":              "CAST(compras AS FLOAT64)",
    "receita":              "CAST(receita AS FLOAT64)",
    "roas":                 "roas",
    "data_inicio":          "CAST(data_inicio AS STRING)",
    "data_fim":             "CAST(data_fim AS STRING)",
    "criado_em":            "criado_em",
}))

# ── dooki_pedidos_diario ────────────────────────────────────────────────────
blocks.append(block("dooki_pedidos_diario", {
    "origem":               "'dooki_pedidos_diario'",
    "cliente":              "'pod_kombucha'",
    "plataforma":           "fonte",
    "data":                 "CAST(data AS STRING)",
    "compras":              "CAST(pedidos AS FLOAT64)",
    "receita":              "CAST(receita AS FLOAT64)",
    "pedidos":              "pedidos",
    "ticket_medio":         "CAST(ticket_medio AS FLOAT64)",
    "clientes_unicos":      "clientes_unicos",
    "criado_em":            "atualizado_em",
}))

# ── metricas_sla ────────────────────────────────────────────────────────────
blocks.append(block("metricas_sla", {
    "origem":               "'metricas_sla'",
    "plataforma":           "tipo",
    "data":                 "CAST(DATE(gerado_em) AS STRING)",
    "compras":              "CAST(total AS FLOAT64)",
    "janela_dias":          "CAST(janela_dias AS FLOAT64)",
    "tipo_sla":             "tipo",
    "total_sla":            "CAST(total AS FLOAT64)",
    "abertos":              "abertos",
    "resolvidos":           "resolvidos",
    "avg_dias":             "avg_dias",
    "sla_dias":             "sla_dias",
    "dentro_sla":           "dentro_sla",
    "sla_vencidos":         "sla_vencidos",
    "ic_low_pct":           "ic_low_pct",
    "ic_high_pct":          "ic_high_pct",
    "confiavel":            "confiavel",
    "atualizado_em":        "atualizado_em",
    "criado_em":            "gerado_em",
}))

# ── tickets_alerta ──────────────────────────────────────────────────────────
blocks.append(block("tickets_alerta", {
    "origem":               "'tickets_alerta'",
    "cliente":              "cliente",
    "plataforma":           "tipo",
    "data":                 "CAST(reuniao_data AS STRING)",
    "status":               "status",
    "ticket_id":            "ticket_id",
    "fonte_id":             "fonte_id",
    "tipo_sla":             "tipo",
    "reuniao_titulo":       "reuniao_titulo",
    "reuniao_data":         "reuniao_data",
    "descricao":            "descricao",
    "trecho":               "trecho",
    "resolvido_em":         "resolvido_em",
    "resolvido_por":        "resolvido_por",
    "evidencia":            "evidencia",
    "aberto_em":            "aberto_em",
    "atualizado_em":        "atualizado_em",
    "criado_em":            "aberto_em",
}))

# ── analise_reunioes ────────────────────────────────────────────────────────
blocks.append(block("analise_reunioes", {
    "origem":               "'analise_reunioes'",
    "data":                 "CAST(data AS STRING)",
    "fonte_id":             "fonte_id",
    "reuniao_titulo":       "titulo",
    "reuniao_data":         "data",
    "sentimento":           "sentimento",
    "alertas":              "alertas",
    "clientes_mencionados": "clientes_mencionados",
    "resumo_analise":       "resumo_analise",
    "analisado_em":         "analisado_em",
    "atualizado_em":        "atualizado_em",
    "criado_em":            "analisado_em",
}))


# ── Monta DDL final ─────────────────────────────────────────────────────────
ddl = (
    f"CREATE OR REPLACE VIEW `{P}.{D}.v_todos_dados` AS\n\n"
    + "\n\nUNION ALL\n\n".join(blocks)
)

# Validação local antes de enviar
print(f"Total colunas canônicas: {N}")
for i, blk in enumerate(blocks):
    n_as = blk.count("\n  ") - 1  # conta linhas de col dentro do SELECT
    table = blk.split("\n")[0].replace("-- ── ", "").split(" ")[0]
    # conta AS em linhas de SELECT (cada linha tem exatamente 1 AS alias)
    sel_lines = [l for l in blk.split("\n") if l.startswith("  ") and " AS " in l]
    status = "✅" if len(sel_lines) == N else f"❌ ({len(sel_lines)} != {N})"
    print(f"  Block {i+1:02d} {table:<35} {status}")

print("\nEnviando ao BigQuery...")
bq = bigquery.Client(project=P)
job = bq.query(ddl)
job.result()
print(f"✅ v_todos_dados criada com sucesso! ({N} colunas × {len(blocks)} tabelas)")
