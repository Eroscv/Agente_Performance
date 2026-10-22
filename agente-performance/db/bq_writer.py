"""
db/bq_writer.py

Dual-write: após salvar no SQLite, espelha os dados no BigQuery.

Arquitetura:
  SQLite  ← escrita primária (sync, logs, offline)
  BigQuery ← espelho analítico (Looker Studio, cross-cliente, histórico)

API pública:
  write_ads(rows, cliente, plataforma)        → espelha linhas de ads no BQ
  write_shopify(rows, cliente)                → espelha receita Shopify no BQ
  write_novos_clientes_semanal(rows, cliente) → snapshot semanal (MERGE, semana fechada nunca sobrescrita)
  enabled()                                   → True se BQ está configurado

O módulo é silencioso em caso de falha — nunca bloqueia o sync principal.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_BQ_CLIENT = None


def enabled() -> bool:
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    return bool(creds and Path(creds).exists())


def _client():
    global _BQ_CLIENT
    if _BQ_CLIENT is None:
        from google.cloud import bigquery
        _BQ_CLIENT = bigquery.Client()
    return _BQ_CLIENT


def _insert(table_id: str, rows: list[dict], desc: str) -> int:
    """
    Faz MERGE (upsert) no BQ usando uma tabela temporária.
    Garante que não há duplicatas na tabela destino.
    
    Para ads_diario: chave única = (cliente, plataforma, data, campaign_id, adset_id, ad_id)
    Para shopify_diario: chave única = (cliente, data)
    Retorna número de erros.
    """
    if not rows or not enabled():
        return 0
    try:
        from google.cloud import bigquery
        bq = _client()
        project = os.environ.get("BQ_PROJECT", bq.project)
        dataset = os.environ.get("BQ_DATASET", "guara_performance")
        full_table = f"{project}.{dataset}.{table_id}"

        # Chaves de merge por tabela
        # Para ads_diario, a chave varia por plataforma:
        #   meta   → chave completa com ad_id (anúncio)
        #   google → apenas até campaign_id (Google Ads é por campanha, sem ad_id)
        if table_id == "ads_diario":
            # Detecta plataforma pelo primeiro row
            plataforma_rows = (rows[0].get("plataforma") or "") if rows else ""
            if plataforma_rows == "google":
                keys = ["cliente", "plataforma", "data", "campaign_id"]
            else:
                keys = ["cliente", "plataforma", "data", "campaign_id", "adset_id", "ad_id"]
        else:
            merge_keys = {
                "shopify_diario":             ["cliente", "data"],
                "abandoned_checkout_detail":  ["checkout_id", "cliente"],
                "abandoned_checkout_daily":   ["cliente", "data", "utm_source_norm"],
                "meta_campaign_totals":       ["cliente", "data_inicio", "data_fim", "campaign_id"],
                "pedidos_diario":             ["cliente", "order_id"],
                "novos_clientes_semanal":     ["cliente", "semana_inicio", "semana_fim"],
            }
            keys = merge_keys.get(table_id, [])

        if not keys:
            # fallback: insert simples para tabelas sem chave definida
            table = bq.get_table(full_table)
            errors = []
            for i in range(0, len(rows), 500):
                errs = bq.insert_rows_json(table, rows[i:i+500])
                errors.extend(errs)
            if errors:
                logger.warning("[BQ] %s — %d erros: %s", desc, len(errors), errors[:1])
            else:
                logger.info("[BQ] ✅ %s — %d linhas inseridas", desc, len(rows))
            return len(errors)

        # MERGE via tabela temporária
        tmp_table = f"{project}.{dataset}._{table_id}_tmp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        schema = bq.get_table(full_table).schema

        # Cria tabela temporária com TTL de 1 hora (garante limpeza mesmo em falha)
        tmp_ref = bigquery.Table(tmp_table, schema=schema)
        tmp_ref.expires = datetime.now(timezone.utc).replace(microsecond=0) + __import__('datetime').timedelta(hours=1)
        bq.create_table(tmp_ref, exists_ok=True)

        # Insere na temp via load_table_from_json (batch load, não streaming)
        # Streaming (insert_rows_json) tem buffer de ~1 min invisível para MERGE/DML
        # load_table_from_json é síncrono: dado disponível imediatamente após o job
        import json as _json
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition="WRITE_APPEND",
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        ndjson = "\n".join(_json.dumps(r, default=str) for r in rows)
        load_job = bq.load_table_from_json(
            rows, tmp_table, job_config=job_config
        )
        load_job.result()  # aguarda conclusão
        if load_job.errors:
            logger.warning("[BQ] %s — erro ao popular temp: %s", desc, load_job.errors[:1])
            bq.delete_table(tmp_table, not_found_ok=True)
            return len(load_job.errors)

        # MERGE: atualiza se existe, insere se não existe
        on_clause = " AND ".join(f"(T.{k} IS NOT DISTINCT FROM S.{k})" for k in keys)
        all_cols = [f.name for f in schema]
        update_cols = [c for c in all_cols if c not in keys]

        # shopify_diario: colunas de sessão são gravadas por write_shopify_sessoes (MERGE separado).
        # O write_shopify (pedidos) envia NULL nessas colunas — usar COALESCE para preservar
        # o valor existente no target em vez de sobrescrever com NULL.
        _SESSOES_COLS = {
            "sessoes", "sessoes_com_carrinho", "sessoes_checkout",
            "sessoes_compra", "taxa_conversao", "is_bot_suspect",
        }
        if table_id == "shopify_diario":
            update_clause = ", ".join(
                f"T.{c} = COALESCE(S.{c}, T.{c})" if c in _SESSOES_COLS
                else f"T.{c} = S.{c}"
                for c in update_cols
            )
        else:
            update_clause = ", ".join(f"T.{c} = S.{c}" for c in update_cols)

        insert_cols = ", ".join(all_cols)
        insert_vals = ", ".join(f"S.{c}" for c in all_cols)

        merge_sql = f"""
        MERGE `{full_table}` T
        USING `{tmp_table}` S
        ON {on_clause}
        WHEN MATCHED THEN
          UPDATE SET {update_clause}
        WHEN NOT MATCHED THEN
          INSERT ({insert_cols}) VALUES ({insert_vals})
        """
        try:
            bq.query(merge_sql).result()
        finally:
            # Garante remoção da tmp mesmo em caso de falha no MERGE
            bq.delete_table(tmp_table, not_found_ok=True)

        logger.info("[BQ] ✅ %s — MERGE %d linhas OK", desc, len(rows))
        return 0
    except Exception as exc:
        logger.warning("[BQ] %s — falha silenciosa: %s", desc, exc)
        return -1


def write_ads(
    rows: list[dict],
    cliente: str,
    plataforma: Literal["meta", "google"],
) -> None:
    """
    Espelha linhas de ads no BigQuery após sync SQLite.

    Args:
        rows: lista de dicts no formato interno do loader (keys = colunas SQLite)
        cliente: 'source_of' | 'pod_kombucha'
        plataforma: 'meta' | 'google'
    """
    if not rows or not enabled():
        return

    agora = datetime.now(timezone.utc).isoformat()
    bq_rows = []

    for r in rows:
        if plataforma == "meta":
            inv  = r.get("investimento") or 0
            comp = r.get("compras") or 0
            rec  = r.get("valor_compras") or 0
            bq_rows.append({
                "cliente":             cliente,
                "plataforma":          "meta",
                "data":                r.get("data"),
                "campanha":            r.get("campanha"),
                "campaign_id":         r.get("campaign_id"),
                "adset_name":          r.get("adset_name"),
                "adset_id":            r.get("adset_id"),
                "ad_name":             r.get("ad_name"),
                "ad_id":               r.get("ad_id"),
                "objetivo":            r.get("objetivo"),
                "status":              r.get("status_criativo"),
                "investimento":        str(round(inv, 2)),
                "compras":             float(comp),
                "receita":             str(round(rec, 2)),
                "impressoes":          r.get("impressoes"),
                "alcance":             r.get("alcance"),
                "cliques_link":        r.get("cliques_link"),
                "cliques_todos":       r.get("cliques_todos"),
                "roas":                r.get("roas_compras"),
                "cpa":                 r.get("cpa_meta"),
                "frequencia":          r.get("frequencia"),
                "video_3s":            r.get("video_3s"),
                "video_50pct":         r.get("video_50pct"),
                "thruplays":           r.get("thruplays"),
                "adicoes_carrinho":    r.get("adicoes_carrinho"),
                "checkout_iniciado":   r.get("checkout_iniciado"),
                "visualizacoes_landing": r.get("visualizacoes_landing"),
                "qualidade_ranking":   r.get("qualidade_ranking"),
                "engagement_ranking":  r.get("engagement_ranking"),
                "conversion_ranking":  r.get("conversion_ranking"),
                "account_id":          r.get("account_id"),
                "criado_em":           agora,
            })
        else:  # google
            inv  = r.get("investimento") or 0
            comp = r.get("conversoes") or 0
            rec  = r.get("valor_conversao") or 0
            bq_rows.append({
                "cliente":      cliente,
                "plataforma":   "google",
                "data":         r.get("data"),
                "campanha":     r.get("campanha"),
                "campaign_id":  r.get("campaign_id"),
                "status":       r.get("status"),
                "investimento": str(round(inv, 2)),
                "compras":      float(comp),
                "receita":      str(round(rec, 2)),
                "impressoes":   r.get("impressoes"),
                "cliques_link": r.get("cliques"),
                "cpa":          round(inv / comp, 2) if comp > 0 else 0.0,
                "cpm":          round(inv / r["impressoes"] * 1000, 2) if r.get("impressoes") else None,
                "account_id":   r.get("customer_id"),
                "criado_em":    agora,
            })

    # Filtra nulos de data
    bq_rows = [r for r in bq_rows if r.get("data")]
    _insert("ads_diario", bq_rows, f"{cliente}/{plataforma}")


def write_campaign_totals(
    rows: list[dict],
    cliente: str,
) -> None:
    """
    Escreve totais campaign-level no BQ (meta_campaign_totals).
    Chave de merge: (cliente, data_inicio, data_fim, campaign_id)

    Args:
        rows:    lista de dicts de buscar_totais_campaign()
        cliente: 'source_of' | 'pod_kombucha' etc.
    """
    if not rows or not enabled():
        return

    agora = datetime.now(timezone.utc).isoformat()
    bq_rows = []
    for r in rows:
        inv  = r.get("investimento") or 0
        comp = r.get("compras") or 0
        rec  = r.get("valor_compras") or 0
        bq_rows.append({
            "cliente":      cliente,
            "data_inicio":  r.get("data_inicio"),
            "data_fim":     r.get("data_fim"),
            "campanha":     r.get("campanha"),
            "campaign_id":  r.get("campaign_id"),
            "account_id":   r.get("account_id"),
            "investimento": str(round(inv, 2)),
            "compras":      int(comp),
            "receita":      str(round(rec, 2)),
            "roas":         round(rec / inv, 4) if inv > 0 else None,
            "criado_em":    agora,
        })

    bq_rows = [r for r in bq_rows if r.get("data_inicio") and r.get("data_fim")]
    _insert("meta_campaign_totals", bq_rows, f"campaign_totals/{cliente}")


    """
    Cria ou recria as views analíticas no BigQuery.

    Views criadas:
      v_performance_mensal — consolidado mensal com ROAS real, CPA, retenção,
                             variação MoM e YoY, cruzando ads + shopify.

    Idempotente — usa CREATE OR REPLACE VIEW.
    Silencioso em falha (não bloqueia o agente).
    """
    if not enabled():
        return
    try:
        bq = _client()
        project = os.environ.get("BQ_PROJECT", bq.project)
        dataset = os.environ.get("BQ_DATASET", "guara_performance")
        p = f"`{project}.{dataset}"

        views = {
            "v_performance_mensal": f"""
                CREATE OR REPLACE VIEW `{project}.{dataset}.v_performance_mensal` AS
                WITH ads_mes AS (
                    SELECT
                        cliente,
                        DATE_TRUNC(data, MONTH)                                           AS mes,
                        ROUND(SUM(CASE WHEN plataforma = 'meta'
                                       THEN investimento * 1.1215 ELSE 0 END), 2)        AS investimento_meta,
                        ROUND(SUM(CASE WHEN plataforma = 'google'
                                       THEN investimento ELSE 0 END), 2)                  AS investimento_google,
                        ROUND(SUM(CASE WHEN plataforma = 'meta'
                                       THEN investimento * 1.1215
                                       ELSE investimento END), 2)                         AS investimento_total,
                        ROUND(SUM(CASE WHEN plataforma = 'meta'
                                      THEN compras ELSE 0 END), 4)                        AS compras_meta,
                        ROUND(SUM(CASE WHEN plataforma = 'google'
                                      THEN compras ELSE 0 END), 4)                        AS compras_google,
                        ROUND(SUM(compras), 4)                                            AS compras_total
                    FROM `{project}.{dataset}.ads_diario`
                    GROUP BY 1, 2
                ),
                shopify_dedup AS (
                    -- Deduplica: mantém apenas a linha mais recente por (cliente, data)
                    SELECT * EXCEPT(rn)
                    FROM (
                        SELECT *,
                            ROW_NUMBER() OVER (PARTITION BY cliente, data ORDER BY criado_em DESC) AS rn
                        FROM `{project}.{dataset}.shopify_diario`
                    )
                    WHERE rn = 1
                ),
                shopify_mes AS (
                    SELECT
                        cliente,
                        DATE_TRUNC(data, MONTH)                                           AS mes,
                        ROUND(SUM(receita), 2)                                            AS receita_shopify,
                        SUM(pedidos)                                                      AS pedidos,
                        SUM(novos_clientes)                                               AS novos_clientes,
                        SUM(recompradores)                                                AS recompradores,
                        ROUND(AVG(ticket_medio), 2)                                       AS ticket_medio,
                        ROUND(SUM(total_desconto), 2)                                     AS total_desconto,
                        SUM(pedidos_acima_300)                                            AS pedidos_acima_300
                    FROM shopify_dedup
                    GROUP BY 1, 2
                ),
                base AS (
                    SELECT
                        a.cliente, a.mes,
                        a.investimento_meta, a.investimento_google, a.investimento_total,
                        a.compras_meta, a.compras_google, a.compras_total,
                        s.receita_shopify, s.pedidos, s.novos_clientes, s.recompradores,
                        s.ticket_medio, s.total_desconto, s.pedidos_acima_300,
                        ROUND(s.receita_shopify / NULLIF(a.investimento_total, 0), 2)    AS roas_real,
                        ROUND(a.investimento_total / NULLIF(a.compras_total, 0), 2)      AS cpa,
                        ROUND(s.recompradores / NULLIF(s.pedidos, 0) * 100, 1)          AS pct_retencao,
                        ROUND(s.novos_clientes / NULLIF(s.pedidos, 0) * 100, 1)         AS pct_novos,
                        ROUND(s.pedidos_acima_300 / NULLIF(s.pedidos, 0) * 100, 1)      AS pct_premium
                    FROM ads_mes a
                    LEFT JOIN shopify_mes s USING (cliente, mes)
                )
                SELECT
                    *,
                    LAG(investimento_total) OVER w  AS invest_mes_ant,
                    LAG(receita_shopify)    OVER w  AS receita_mes_ant,
                    LAG(roas_real)          OVER w  AS roas_mes_ant,
                    LAG(compras_total)      OVER w  AS compras_mes_ant,
                    LAG(cpa)               OVER w  AS cpa_mes_ant,
                    ROUND((investimento_total - LAG(investimento_total) OVER w)
                          / NULLIF(LAG(investimento_total) OVER w, 0) * 100, 1) AS var_invest_pct,
                    ROUND((receita_shopify - LAG(receita_shopify) OVER w)
                          / NULLIF(LAG(receita_shopify) OVER w, 0) * 100, 1)   AS var_receita_pct,
                    ROUND((roas_real - LAG(roas_real) OVER w)
                          / NULLIF(LAG(roas_real) OVER w, 0) * 100, 1)         AS var_roas_pct,
                    LAG(receita_shopify, 12) OVER w   AS receita_yoy,
                    LAG(roas_real, 12)       OVER w   AS roas_yoy,
                    LAG(investimento_total, 12) OVER w AS invest_yoy,
                    ROUND((receita_shopify - LAG(receita_shopify, 12) OVER w)
                          / NULLIF(LAG(receita_shopify, 12) OVER w, 0) * 100, 1) AS var_receita_yoy_pct,
                    ROUND((roas_real - LAG(roas_real, 12) OVER w)
                          / NULLIF(LAG(roas_real, 12) OVER w, 0) * 100, 1)     AS var_roas_yoy_pct
                FROM base
                WINDOW w AS (PARTITION BY cliente ORDER BY mes)
                ORDER BY cliente, mes DESC
            """,
        }

        for name, ddl in views.items():
            bq.query(ddl).result()
            logger.info("[BQ] ✅ view %s criada/atualizada", name)

    except Exception as exc:
        logger.warning("[BQ] create_views — falha silenciosa: %s", exc)


def write_shopify(rows: list[dict], cliente: str) -> None:
    """
    Espelha receita diária Shopify no BigQuery.

    Args:
        rows: lista de dicts com colunas da tabela shopify_receita_diaria
        cliente: 'source_of' | 'pod_kombucha'
    """
    if not rows or not enabled():
        return

    agora = datetime.now(timezone.utc).isoformat()
    bq_rows = []
    for r in rows:
        bq_rows.append({
            "cliente":                cliente,
            "data":                   r.get("data"),
            "receita":                str(round(r.get("receita") or 0, 2)),
            "frete":                  str(round(r.get("frete") or 0, 2)),
            "pedidos":                int(r.get("pedidos") or 0),
            "novos_clientes":         int(r.get("novos_clientes") or 0),
            "recompradores":          int(r.get("recompradores") or 0),
            "ticket_medio":           str(round(r.get("ticket_medio") or 0, 2)),
            "total_desconto":         str(round(r.get("total_desconto") or 0, 2)),
            "receita_criada":         str(round(r.get("receita_criada") or 0, 2)),
            "pedidos_criados":        int(r.get("pedidos_criados") or 0),
            "pedidos_meta":           int(r.get("pedidos_meta") or 0),
            "receita_meta":           str(round(r.get("receita_meta") or 0, 2)),
            "novos_meta":             int(r.get("novos_meta") or 0),
            "pedidos_google":         int(r.get("pedidos_google") or 0),
            "receita_google":         str(round(r.get("receita_google") or 0, 2)),
            "novos_google":           int(r.get("novos_google") or 0),
            "pedidos_outros":         int(r.get("pedidos_outros") or 0),
            "receita_outros":         str(round(r.get("receita_outros") or 0, 2)),
            "novos_outros":           int(r.get("novos_outros") or 0),
            "gross_sales":            str(round(r.get("gross_sales") or 0, 2)),
            "pedidos_gt300":          int(r.get("pedidos_gt300") or 0),
            # Sessões e funil (ShopifyQL) — pode ser None quando ausente
            "sessoes":                int(r["sessoes"]) if r.get("sessoes") is not None else None,
            "sessoes_com_carrinho":   int(r["sessoes_com_carrinho"]) if r.get("sessoes_com_carrinho") is not None else None,
            "sessoes_checkout":       int(r["sessoes_checkout"]) if r.get("sessoes_checkout") is not None else None,
            "sessoes_compra":         int(r["sessoes_compra"]) if r.get("sessoes_compra") is not None else None,
            "taxa_conversao":         float(r["taxa_conversao"]) if r.get("taxa_conversao") is not None else None,
            # None quando ausente no row (pedidos sem sessões) → COALESCE no MERGE preserva
            # o valor existente no BQ em vez de sobrescrever com 0.
            "is_bot_suspect":         int(r["is_bot_suspect"]) if r.get("is_bot_suspect") is not None else None,
            "criado_em":              agora,
        })

    bq_rows = [r for r in bq_rows if r.get("data")]
    _insert("shopify_diario", bq_rows, f"shopify/{cliente}")


def write_shopify_sessoes(rows: list[dict], cliente: str) -> None:
    """
    Faz MERGE somente das colunas de sessões no shopify_diario do BQ.
    Usado pelo sync_shopify_sessions.py após gravar no SQLite.

    Args:
        rows: lista de dicts com schema de shopify_sessoes
              (data, sessoes, sessoes_com_carrinho, sessoes_checkout,
               sessoes_compra, taxa_conversao, is_bot_suspect)
        cliente: 'source_of' | 'pod_kombucha'
    """
    if not rows or not enabled():
        return

    agora = datetime.now(timezone.utc).isoformat()
    bq = _client()
    project = os.environ.get("BQ_PROJECT", bq.project)
    dataset = os.environ.get("BQ_DATASET", "guara_performance")
    full_table = f"{project}.{dataset}.shopify_diario"

    # MERGE direto por SQL: atualiza só colunas de sessões, preserva pedidos/receita
    # Usa UNNEST de array de structs — mais eficiente que tabela temporária para poucas linhas
    if not rows:
        return

    # Monta VALUES como literals SQL para UNNEST
    valor_linhas = []
    for r in rows:
        data    = r.get("data", "")
        sess    = r.get("sessoes")
        carr    = r.get("sessoes_com_carrinho")
        check   = r.get("sessoes_checkout")
        comp    = r.get("sessoes_compra")
        conv    = r.get("taxa_conversao")
        bot     = int(r.get("is_bot_suspect") or 0)
        if not data:
            continue
        valor_linhas.append(
            f"('{cliente}', DATE '{data}', "
            f"{sess if sess is not None else 'NULL'}, "
            f"{carr if carr is not None else 'NULL'}, "
            f"{check if check is not None else 'NULL'}, "
            f"{comp if comp is not None else 'NULL'}, "
            f"{conv if conv is not None else 'NULL'}, "
            f"{bot})"
        )

    if not valor_linhas:
        return

    values_sql = ",\n    ".join(valor_linhas)
    merge_sql = f"""
    MERGE `{full_table}` T
    USING (
      SELECT cliente, data, sessoes, sessoes_com_carrinho,
             sessoes_checkout, sessoes_compra, taxa_conversao, is_bot_suspect
      FROM UNNEST([
        STRUCT<cliente STRING, data DATE, sessoes INT64,
               sessoes_com_carrinho INT64, sessoes_checkout INT64,
               sessoes_compra INT64, taxa_conversao FLOAT64,
               is_bot_suspect INT64>
        {values_sql}
      ])
    ) S
    ON T.cliente = S.cliente AND T.data = S.data
    WHEN MATCHED THEN UPDATE SET
      T.sessoes              = S.sessoes,
      T.sessoes_com_carrinho = S.sessoes_com_carrinho,
      T.sessoes_checkout     = S.sessoes_checkout,
      T.sessoes_compra       = S.sessoes_compra,
      T.taxa_conversao       = S.taxa_conversao,
      -- GREATEST preserva is_bot_suspect=1 mesmo que o sync envie 0 (nunca regride)
      T.is_bot_suspect       = GREATEST(COALESCE(T.is_bot_suspect, 0), COALESCE(S.is_bot_suspect, 0))
    WHEN NOT MATCHED THEN INSERT
      (cliente, data, sessoes, sessoes_com_carrinho, sessoes_checkout,
       sessoes_compra, taxa_conversao, is_bot_suspect, criado_em)
      VALUES
      (S.cliente, S.data, S.sessoes, S.sessoes_com_carrinho, S.sessoes_checkout,
       S.sessoes_compra, S.taxa_conversao, S.is_bot_suspect, CURRENT_TIMESTAMP())
    """
    try:
        bq.query(merge_sql).result()
        logger.info("[BQ] ✅ shopify_sessoes/%s — MERGE %d linhas OK", cliente, len(valor_linhas))
    except Exception as exc:
        logger.warning("[BQ] shopify_sessoes/%s — falha silenciosa: %s", cliente, exc)


def write_novos_clientes_semanal(rows: list[dict], cliente: str) -> None:
    """
    Grava snapshot semanal de novos clientes na tabela novos_clientes_semanal.

    Semanas já fechadas NUNCA são sobrescritas — o MERGE usa
    WHEN NOT MATCHED THEN INSERT (sem WHEN MATCHED UPDATE).
    Isso garante que o histórico é imutável após a carga de segunda-feira.

    Args:
        rows: lista de dicts com keys:
              semana_inicio, semana_fim (str YYYY-MM-DD),
              novos_clientes, recompradores, pedidos (int),
              novos_meta, novos_google, novos_outros (int, opcional)
        cliente: 'source_of' | 'pod_kombucha'
    """
    if not rows or not enabled():
        return

    agora = datetime.now(timezone.utc).isoformat()
    bq = _client()
    project = os.environ.get("BQ_PROJECT", bq.project)
    dataset = os.environ.get("BQ_DATASET", "guara_performance")
    full_table = f"{project}.{dataset}.novos_clientes_semanal"

    bq_rows = []
    for r in rows:
        pedidos      = int(r.get("pedidos") or 0)
        novos        = int(r.get("novos_clientes") or 0)
        recomb       = int(r.get("recompradores") or 0)
        novos_meta   = int(r["novos_meta"])   if r.get("novos_meta")   is not None else None
        novos_google = int(r["novos_google"]) if r.get("novos_google") is not None else None
        novos_outros = int(r["novos_outros"]) if r.get("novos_outros") is not None else None
        bq_rows.append({
            "cliente":        cliente,
            "semana_inicio":  r.get("semana_inicio"),
            "semana_fim":     r.get("semana_fim"),
            "novos_clientes": novos,
            "recompradores":  recomb,
            "pedidos":        pedidos,
            "pct_novos":      round(novos / pedidos * 100, 1) if pedidos > 0 else None,
            "pct_retencao":   round(recomb / pedidos * 100, 1) if pedidos > 0 else None,
            "novos_meta":     novos_meta,
            "novos_google":   novos_google,
            "novos_outros":   novos_outros,
            "criado_em":      agora,
        })

    bq_rows = [r for r in bq_rows if r.get("semana_inicio") and r.get("semana_fim")]
    if not bq_rows:
        return

    def _int_or_null(v):
        return "NULL" if v is None else str(int(v))

    try:
        valor_linhas = []
        for r in bq_rows:
            valor_linhas.append(
                f"('{r['cliente']}', DATE '{r['semana_inicio']}', DATE '{r['semana_fim']}', "
                f"{r['novos_clientes']}, {r['recompradores']}, {r['pedidos']}, "
                f"{'NULL' if r['pct_novos'] is None else r['pct_novos']}, "
                f"{'NULL' if r['pct_retencao'] is None else r['pct_retencao']}, "
                f"{_int_or_null(r['novos_meta'])}, "
                f"{_int_or_null(r['novos_google'])}, "
                f"{_int_or_null(r['novos_outros'])}, "
                f"TIMESTAMP '{r['criado_em']}')"
            )

        values_sql = ",\n    ".join(valor_linhas)
        merge_sql = f"""
        MERGE `{full_table}` T
        USING (
          SELECT cliente, semana_inicio, semana_fim, novos_clientes,
                 recompradores, pedidos, pct_novos, pct_retencao,
                 novos_meta, novos_google, novos_outros, criado_em
          FROM UNNEST([
            STRUCT<cliente STRING, semana_inicio DATE, semana_fim DATE,
                   novos_clientes INT64, recompradores INT64, pedidos INT64,
                   pct_novos FLOAT64, pct_retencao FLOAT64,
                   novos_meta INT64, novos_google INT64, novos_outros INT64,
                   criado_em TIMESTAMP>
            {values_sql}
          ])
        ) S
        ON T.cliente = S.cliente
           AND T.semana_inicio = S.semana_inicio
           AND T.semana_fim = S.semana_fim
        WHEN NOT MATCHED THEN
          INSERT (cliente, semana_inicio, semana_fim, novos_clientes,
                  recompradores, pedidos, pct_novos, pct_retencao,
                  novos_meta, novos_google, novos_outros, criado_em)
          VALUES (S.cliente, S.semana_inicio, S.semana_fim, S.novos_clientes,
                  S.recompradores, S.pedidos, S.pct_novos, S.pct_retencao,
                  S.novos_meta, S.novos_google, S.novos_outros, S.criado_em)
        """
        bq.query(merge_sql).result()
        logger.info("[BQ] ✅ novos_clientes_semanal/%s — %d semana(s) gravada(s)", cliente, len(bq_rows))
    except Exception as exc:
        logger.warning("[BQ] novos_clientes_semanal/%s — falha silenciosa: %s", cliente, exc)
