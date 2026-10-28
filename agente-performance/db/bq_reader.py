"""
db/bq_reader.py

Camada de LEITURA analítica do BigQuery — fonte oficial de dados.

Arquitetura:
  BigQuery ← fonte primária para alertas, relatórios e queries analíticas
  SQLite   ← escrita local / fallback quando BQ indisponível

API pública:
  query_bq(sql, params)          → list[dict] — executa query parametrizada no BQ
  query_bq_one(sql, params)      → dict | None — primeiro resultado
  enabled()                      → True se credenciais BQ estão configuradas
  alertas_roas_bq(cliente_key, threshold, janela_dias)
                                 → list[dict] — campanhas com ROAS abaixo do threshold
  data_maxima_bq(cliente_key)    → str | None — data mais recente em ads_diario
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BQ_CLIENT = None

PROJECT = os.environ.get("BQ_PROJECT", "guara-db")
DATASET = os.environ.get("BQ_DATASET", "guara_performance")
ADS_TABLE = f"`{PROJECT}.{DATASET}.ads_diario`"
V_ALERTAS = f"`{PROJECT}.{DATASET}.v_alertas_campanha`"


def enabled() -> bool:
    """True se credenciais BQ estão configuradas e o arquivo existe."""
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    return bool(creds and Path(creds).exists())


def _client():
    """Retorna singleton do BigQuery Client."""
    global _BQ_CLIENT
    if _BQ_CLIENT is None:
        from google.cloud import bigquery
        _BQ_CLIENT = bigquery.Client()
    return _BQ_CLIENT


def query_bq(sql: str, params: Optional[list] = None) -> list[dict]:
    """
    Executa query no BigQuery e retorna lista de dicts.

    Suporta parâmetros posicionais via @p0, @p1, ... no SQL.
    Exemplo:
        query_bq("SELECT * FROM t WHERE cliente = @p0", ["source_of"])
    """
    if not enabled():
        logger.warning("[BQReader] BigQuery não configurado — retornando []")
        return []
    try:
        from google.cloud import bigquery
        bq = _client()

        job_config = bigquery.QueryJobConfig()
        if params:
            job_config.query_parameters = [
                bigquery.ScalarQueryParameter(f"p{i}", _bq_type(v), v)
                for i, v in enumerate(params)
            ]

        rows = list(bq.query(sql, job_config=job_config).result())
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("[BQReader] Erro na query: %s\nSQL: %.300s", exc, sql)
        return []


def query_bq_one(sql: str, params: Optional[list] = None) -> Optional[dict]:
    """Retorna primeiro resultado ou None."""
    rows = query_bq(sql, params)
    return rows[0] if rows else None


def _bq_type(value) -> str:
    """Infere o tipo BigQuery de um valor Python."""
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    if isinstance(value, date):
        return "DATE"
    return "STRING"


def data_maxima_bq(cliente_key: Optional[str] = None) -> Optional[str]:
    """
    Retorna a data mais recente disponível em ads_diario, opcionalmente
    filtrada por cliente.
    """
    if cliente_key:
        row = query_bq_one(
            f"SELECT FORMAT_DATE('%Y-%m-%d', MAX(data)) AS dm FROM {ADS_TABLE} WHERE cliente = @p0",
            [cliente_key],
        )
    else:
        row = query_bq_one(
            f"SELECT FORMAT_DATE('%Y-%m-%d', MAX(data)) AS dm FROM {ADS_TABLE}",
        )
    return row.get("dm") if row else None


def alertas_roas_bq(
    cliente_key: Optional[str] = None,
    threshold: float = 2.0,
    janela_dias: int = 1,
    invest_min: float = 100.0,
) -> list[dict]:
    """
    Retorna campanhas com ROAS abaixo do threshold no período recente.

    Usa v_alertas_campanha para aproveitar a média móvel 7d já calculada no BQ.
    Filtra pela data_maxima disponível (dado mais recente após sync).

    Args:
        cliente_key:  ex 'source_of' — None = todos os clientes
        threshold:    ROAS mínimo aceitável (ex: 2.0)
        janela_dias:  quantos dias recentes analisar (padrão: 1 = só o último dia)
        invest_min:   investimento mínimo para aparecer no alerta (R$)

    Returns:
        list[dict] com campos: campanha, cliente, plataforma, data, investimento,
                               receita, compras, roas, roas_media_7d, status_alerta
    """
    data_max = data_maxima_bq(cliente_key)
    if not data_max:
        logger.warning("[BQReader] alertas_roas_bq: sem dados no BQ")
        return []

    data_inicio = (
        date.fromisoformat(data_max) - timedelta(days=janela_dias - 1)
    ).isoformat()

    params = [data_inicio, data_max, invest_min, threshold]
    cliente_filter = ""
    if cliente_key:
        cliente_filter = "AND cliente = @p4"
        params.append(cliente_key)

    sql = f"""
        SELECT
            cliente,
            plataforma,
            campanha,
            data,
            ROUND(CAST(investimento AS FLOAT64), 2)   AS investimento,
            ROUND(CAST(receita AS FLOAT64), 2)        AS receita,
            ROUND(COALESCE(compras, 0), 0)            AS compras,
            ROUND(COALESCE(roas, 0), 4)               AS roas,
            ROUND(COALESCE(roas_media_7d, 0), 4)      AS roas_media_7d,
            status_alerta
        FROM {V_ALERTAS}
        WHERE data BETWEEN @p0 AND @p1
          AND investimento >= @p2
          AND (roas IS NULL OR roas < @p3)
          AND campanha IS NOT NULL
          {cliente_filter}
        ORDER BY investimento DESC
        LIMIT 20
    """

    rows = query_bq(sql, params)
    logger.info("[BQReader] alertas_roas_bq: %d campanhas abaixo de %.2f (data_max=%s)",
                len(rows), threshold, data_max)
    return rows


def campanhas_sem_conversao_bq(
    cliente_key: Optional[str] = None,
    janela_dias: int = 7,
    invest_min: float = 50.0,
) -> list[dict]:
    """
    Campanhas com investimento mas ZERO compras nos últimos N dias (via BQ).
    """
    data_max = data_maxima_bq(cliente_key)
    if not data_max:
        return []

    data_inicio = (
        date.fromisoformat(data_max) - timedelta(days=janela_dias - 1)
    ).isoformat()

    params = [data_inicio, data_max, invest_min]
    cliente_filter = ""
    if cliente_key:
        cliente_filter = "AND cliente = @p3"
        params.append(cliente_key)

    sql = f"""
        SELECT
            cliente,
            plataforma,
            campanha,
            ROUND(SUM(CAST(investimento AS FLOAT64)), 2)  AS investimento,
            ROUND(SUM(COALESCE(impressoes, 0)), 0)        AS impressoes,
            ROUND(SUM(COALESCE(cliques_link, 0)), 0)      AS cliques,
            MAX(FORMAT_DATE('%Y-%m-%d', data))            AS ultima_atividade
        FROM {ADS_TABLE}
        WHERE data BETWEEN @p0 AND @p1
          AND investimento > 0
          AND campanha IS NOT NULL
          {cliente_filter}
        GROUP BY cliente, plataforma, campanha
        HAVING SUM(CAST(investimento AS FLOAT64)) >= @p2
           AND (SUM(COALESCE(compras, 0)) = 0 OR SUM(COALESCE(receita, 0)) = 0)
        ORDER BY investimento DESC
        LIMIT 10
    """
    return query_bq(sql, params)
