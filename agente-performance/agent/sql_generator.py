"""
agent/sql_generator.py — Geração dinâmica de SQL via LLM (Text-to-SQL).

Usado como fallback quando nenhum intent cobre a pergunta do usuário.

Fluxo simples (perguntas diretas):
  pergunta → [LLM: Generator] → SELECT validado → executa no BigQuery

Fluxo com Query Planner (perguntas complexas):
  pergunta → [LLM: Planner] → QueryPlan (JSON) → [LLM: Generator] → SELECT validado → executa

O Planner é ativado automaticamente por heurística quando a pergunta contém
palavras-chave de comparação, multi-filtro ou múltiplas dimensões.

Guardrails:
  - Apenas SELECT (bloqueia DDL/DML)
  - LIMIT máx 50 (forçado se ausente)
  - WHERE cliente obrigatório (isolamento entre clientes)
  - Timeout de 8s na execução
  - Erros de coluna → capturado com mensagem amigável

Fonte de dados: BigQuery (guara-db.guara_performance)
"""

import json
import logging
import re
import time
from typing import Optional

import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

from db.connections import query_fed as _query_fed

logger = logging.getLogger(__name__)

def _call_llm(system: str, user: str, max_tokens: int = 512) -> str:
    """Geração de SQL: Gemini → Claude (utils/llm.py). Temperatura 0: SQL não precisa de criatividade."""
    from utils.llm import gerar
    return gerar(system, user, max_tokens=max_tokens, temperature=0.0)


# ── Schema injetado no prompt (BigQuery — fonte oficial) ──────────────────────
_SCHEMA_CONTEXT = """
Banco de dados BigQuery (projeto: guara-db, dataset: guara_performance).

== TABELA PRINCIPAL: `guara-db.guara_performance.ads_diario` ==
Usar para: análises de campanha, ROAS, investimento, compras por data.
Colunas disponíveis:
  cliente           STRING  — identificador do cliente: 'source_of', 'pod_kombucha' ou 'botica'
  plataforma        STRING  — 'meta_ads' ou 'google_ads'
  data              DATE    — data do registro (ex: '2026-08-11')
  campanha          STRING  — nome da campanha
  campaign_id       STRING  — ID da campanha na plataforma
  adset_name        STRING  — nome do conjunto de anúncios
  ad_name           STRING  — nome do anúncio
  objetivo          STRING  — objetivo da campanha (OUTCOME_SALES, etc.)
  status            STRING  — status da campanha (ACTIVE, PAUSED, etc.)
  investimento      NUMERIC — gasto total em R$
  compras           FLOAT   — número de compras/conversões
  receita           NUMERIC — receita gerada em R$ (atribuição da plataforma)
  impressoes        FLOAT   — impressões totais
  alcance           FLOAT   — pessoas únicas alcançadas
  cliques_link      FLOAT   — cliques no link
  cliques_todos     FLOAT   — todos os cliques (inclusive engajamento)
  roas              FLOAT   — ROAS bruto da plataforma (receita/investimento) — não usar em SUM()
  cpa               FLOAT   — custo por aquisição — não usar em SUM()
  cpm               FLOAT   — custo por mil impressões
  frequencia        FLOAT   — impressões / alcance
  video_3s          FLOAT   — visualizações de vídeo de 3 segundos
  video_50pct       FLOAT   — visualizações de vídeo até 50%
  thruplays         FLOAT   — visualizações completas de vídeo
  adicoes_carrinho  FLOAT   — adições ao carrinho
  checkout_iniciado FLOAT   — checkouts iniciados
  visualizacoes_landing FLOAT — visualizações da landing page
  qualidade_ranking STRING  — ranking de qualidade do anúncio
  engagement_ranking STRING — ranking de engajamento
  conversion_ranking STRING — ranking de conversão

== VIEW AUXILIAR: `guara-db.guara_performance.v_ads_roas_real` ==
Usar para: ROAS real (ajustado por Shopify), CPA real.
Colunas adicionais além das de ads_diario:
  investimento_ajustado NUMERIC — investimento com fator de ajuste
  roas_real             NUMERIC — ROAS calculado com receita Shopify real
  cpa_real              FLOAT   — CPA calculado com investimento ajustado

== VIEW AUXILIAR: `guara-db.guara_performance.v_performance_completa` ==
Usar para: visão consolidada mensal por cliente (sem granularidade de campanha).
Colunas:
  cliente            STRING  — chave do cliente
  mes                DATE    — primeiro dia do mês
  investimento_meta  NUMERIC — investimento Meta Ads no mês
  investimento_google NUMERIC — investimento Google Ads no mês
  investimento_total NUMERIC — total gasto no mês
  compras_meta       FLOAT   — compras atribuídas ao Meta
  compras_google     FLOAT   — compras atribuídas ao Google
  receita_shopify    NUMERIC — receita real do Shopify
  pedidos            INTEGER — pedidos Shopify no mês
  novos_clientes     INTEGER — novos clientes no mês
  ticket_medio       NUMERIC — ticket médio no mês
  roas_real_total    NUMERIC — ROAS real total (Shopify / investimento total)

REGRAS OBRIGATÓRIAS:
  1. SEMPRE incluir WHERE cliente = '<valor>' para isolar o cliente
     Valores válidos: 'source_of', 'pod_kombucha', 'botica'
  2. SEMPRE incluir LIMIT (máximo 50)
  3. ROAS calculado como: ROUND(SUM(receita)/NULLIF(SUM(investimento),0), 2)
     — NUNCA use AVG(roas) nem SUM(roas) — a coluna roas não pode ser somada diretamente
  4. CPA calculado como: ROUND(SUM(investimento)/NULLIF(SUM(compras),0), 2)
  5. CTR como: ROUND(SUM(cliques_link)/NULLIF(SUM(impressoes),0)*100, 2)
  6. Filtros de data: data >= '2026-08-01' AND data <= '2026-08-31'
  7. Apenas SELECT — nenhum INSERT/UPDATE/DELETE/DROP; nunca use OR (use coluna IN ('a', 'b'))
  8. Nomes de tabelas sempre com backtick: `guara-db.guara_performance.ads_diario`
  9. NÃO use campanhas_unificada nem cliente_key — não existem no BQ
  10. HAVING com múltiplas agregações: prefira subquery wrapper:
      SELECT * FROM (SELECT ..., SUM(x) AS x_total ... GROUP BY ...) WHERE x_total >= 100
"""

# ── Prompt de geração de SQL (modo direto) ────────────────────────────────────
_SQL_GEN_SYSTEM = (
    "Você é um especialista em SQL para BigQuery, análise de campanhas de mídia paga.\n"
    "Gere APENAS o SQL — sem explicações, sem markdown, sem bloco de código.\n"
    "Responda somente com a query SQL pura, começando em SELECT ou WITH.\n\n"
    + _SCHEMA_CONTEXT
)

# ── Prompt do Query Planner ───────────────────────────────────────────────────
_PLANNER_SYSTEM = """\
Você é um analista de dados especializado em mídia paga.
Sua tarefa é interpretar uma pergunta em português e produzir um plano de consulta estruturado em JSON.

Responda APENAS com JSON válido — sem explicações, sem markdown, sem bloco de código.

Schema do JSON de saída:
{
  "tipo": string,           // "ranking" | "comparativo_temporal" | "filtrado" | "tendencia" | "funil" | "geral"
  "metricas": [string],     // ex: ["roas", "investimento", "compras"] — use nomes das colunas
  "granularidade": string,  // "campanha" | "plataforma" | "dia" | "semana" | "mes" | null
  "periodo_a": {            // período principal (ou único)
    "inicio": string | null,  // ISO: "2026-08-11"
    "fim": string | null
  },
  "periodo_b": {            // período de comparação (só para comparativo_temporal)
    "inicio": string | null,
    "fim": string | null
  } | null,
  "filtros": [              // filtros adicionais (ex: ROAS > 2, plataforma = meta_ads)
    {
      "coluna": string,
      "op": string,         // ">" | "<" | ">=" | "<=" | "=" | "!=" | "IN"
      "valor": string | number | [string]
    }
  ],
  "ordenacao": string | null,  // ex: "roas DESC" | "investimento ASC"
  "limite": number,            // padrão 20, máximo 50
  "usar_having": boolean       // true se os filtros se aplicam a agregações
}

Regras:
- Para ROAS use coluna "roas" (será calculado como SUM(receita)/SUM(investimento))
- Para CPA use coluna "cpa" (será calculado como SUM(investimento)/SUM(compras))
- Campo de filtro por cliente é "cliente" (não "cliente_key")
- Nunca invente colunas fora do schema
- Se período não mencionado, deixe inicio/fim null
- Se não há comparação, deixe periodo_b null
"""

# ── Prompt do Generator (modo planner) ───────────────────────────────────────
_SQL_FROM_PLAN_SYSTEM = (
    "Você é um especialista em SQL para BigQuery, análise de campanhas de mídia paga.\n"
    "Receberá um plano de consulta em JSON e deve gerar APENAS o SQL correspondente.\n"
    "Sem explicações, sem markdown, sem bloco de código — apenas o SELECT ou WITH.\n\n"
    + _SCHEMA_CONTEXT
    + """
INSTRUÇÕES PARA COMPARATIVO TEMPORAL:
  - Use dois CTEs (periodo_a, periodo_b) com os respectivos filtros de data
  - Faça JOIN pelos campos de granularidade
  - Calcule delta absoluto e percentual para cada métrica
  - Exemplo:
    WITH periodo_a AS (
      SELECT campanha,
             SUM(investimento) AS inv_a,
             SUM(receita) AS rec_a
      FROM `guara-db.guara_performance.ads_diario`
      WHERE cliente = 'source_of'
        AND data >= '2026-08-11' AND data <= '2026-08-17'
      GROUP BY campanha
    ), periodo_b AS (
      SELECT campanha,
             SUM(investimento) AS inv_b,
             SUM(receita) AS rec_b
      FROM `guara-db.guara_performance.ads_diario`
      WHERE cliente = 'source_of'
        AND data >= '2026-08-04' AND data <= '2026-08-10'
      GROUP BY campanha
    )
    SELECT a.campanha,
           ROUND(a.rec_a/NULLIF(a.inv_a,0),2) AS roas_atual,
           ROUND(b.rec_b/NULLIF(b.inv_b,0),2) AS roas_anterior,
           ROUND((a.rec_a/NULLIF(a.inv_a,0) - b.rec_b/NULLIF(b.inv_b,0)),2) AS delta_roas
    FROM periodo_a a LEFT JOIN periodo_b b ON a.campanha = b.campanha
    ORDER BY roas_atual DESC
    LIMIT 20
"""
)

# ── Heurística de complexidade ────────────────────────────────────────────────
_COMPLEX_TRIGGERS = re.compile(
    r"\b("
    r"compar[ae]|vs\.?|versus|contra"
    r"|semana\s+passada|m[eê]s\s+passado|per[ií]odo\s+anterior"
    r"|em\s+rela[cç][aã]o\s+a|em\s+compara[cç][aã]o"
    r"|excluindo|apenas\s+(?:campanhas?|plataforma)"
    r"|por\s+plataforma\s+e\s+campanha|por\s+campanha\s+e\s+plataforma"
    r"|acima\s+de\s+\d+.*e\s+(?:abaixo|menor)"
    r"|tanto.*quanto|tanto.*como"
    r"|semana\s+(?:a\s+semana|over\s+semana|vs)"
    r"|m[eê]s\s+(?:a\s+m[eê]s|over\s+m[eê]s)"
    r")\b",
    re.IGNORECASE,
)


def _is_complex_query(pergunta: str) -> bool:
    """Retorna True se a pergunta tem padrões de complexidade que se beneficiam do Planner."""
    return bool(_COMPLEX_TRIGGERS.search(pergunta))


# ── Query Planner ─────────────────────────────────────────────────────────────

def _run_planner(
    pergunta: str,
    cliente: str,
    data_inicio: Optional[str],
    data_fim: Optional[str],
) -> dict:
    """
    Chama o LLM Planner e retorna o QueryPlan como dict.
    Lança ValueError se o JSON for inválido.
    """
    periodo_ctx = ""
    if data_inicio or data_fim:
        periodo_ctx = f"\nPeríodo de referência: {data_inicio or '?'} até {data_fim or 'hoje'}"

    user_msg = (
        f"Cliente: {cliente}{periodo_ctx}\n"
        f"Pergunta: {pergunta}\n\n"
        "Produza o QueryPlan JSON conforme o schema."
    )

    logger.info("[QueryPlanner] Planejando: %r", pergunta[:80])
    raw = _call_llm(system=_PLANNER_SYSTEM, user=user_msg, max_tokens=512)

    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        raise ValueError(f"Planner não retornou JSON válido: {raw[:200]}")

    try:
        plan = json.loads(json_match.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON do Planner inválido: {e} | raw={raw[:200]}")

    logger.info("[QueryPlanner] Plano: %s", json.dumps(plan, ensure_ascii=False))
    return plan


def _sql_from_plan(
    plan: dict,
    pergunta_original: str,
    cliente: str,
) -> str:
    """
    Chama o LLM Generator com o QueryPlan serializado para gerar o SQL final.
    """
    user_msg = (
        f"Cliente: {cliente}\n"
        f"Pergunta original: {pergunta_original}\n\n"
        f"QueryPlan:\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
        "Gere o SQL correspondente. Apenas o SELECT ou WITH, nada mais."
    )

    logger.info("[QueryPlanner] Gerando SQL a partir do plano")
    return _call_llm(system=_SQL_FROM_PLAN_SYSTEM, user=user_msg, max_tokens=700)


# ── Validação ─────────────────────────────────────────────────────────────────
_MAX_ROWS = 50
_FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|PRAGMA|VACUUM|MERGE|TRUNCATE|GRANT|REVOKE"
    r"|EXPORT|CALL|EXECUTE|DECLARE|SET|BEGIN|LOAD)\b|;",
    re.IGNORECASE,
)
# Todo literal comparado a `cliente` tem de ser o cliente da conversa; IN/<>/LIKE/OR em cliente são recusados.
_CLIENTE_LIT_RE = re.compile(r"\bcliente\s*=\s*'([^']*)'", re.IGNORECASE)
_CLIENTE_RUIM_RE = re.compile(r"\bcliente\s*(!=|<>|\bIN\b|\bLIKE\b|\bIS\b)|\bcliente\s*=\s*[^'\s]", re.IGNORECASE)
_LIMIT_RE    = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_CLIENTE_RE  = re.compile(r"\bcliente\s*=", re.IGNORECASE)


def _validate_sql(sql: str, cliente: str) -> tuple[str, str | None]:
    """
    Valida e normaliza o SQL gerado pelo LLM.
    Retorna (sql_limpo, erro_ou_None).
    """
    sql = sql.strip().rstrip(";")

    # Remove markdown leak (```sql ... ```)
    sql = re.sub(r"^```\w*\s*", "", sql)
    sql = re.sub(r"```\s*$", "", sql).strip()

    # BQ não suporta FULL OUTER JOIN — converte para LEFT JOIN
    sql = re.sub(r"\bFULL\s+OUTER\s+JOIN\b", "LEFT JOIN", sql, flags=re.IGNORECASE)

    # Extrai só a partir de WITH ou SELECT
    match = re.search(r"((?:WITH\b|SELECT\b).*)", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return sql, "SQL gerado não contém SELECT."
    sql = match.group(1).strip()

    if _FORBIDDEN_RE.search(sql):
        return sql, "SQL gerado contém operação não permitida (DDL/DML ou múltiplas instruções)."

    # Isolamento entre clientes: nenhum outro cliente pode aparecer na consulta.
    if _CLIENTE_RUIM_RE.search(sql) or any(v != cliente for v in _CLIENTE_LIT_RE.findall(sql)):
        return sql, "SQL gerado consulta outro cliente."
    # `cliente = 'x' AND a OR b` escapa do filtro pela precedência. ponytail: recusa OR em vez de
    # parsear o SQL; o prompt pede IN (...). Isolamento forte = row access policy por cliente no BQ.
    if re.search(r"\bOR\b", sql, re.IGNORECASE):
        return sql, "SQL gerado usa OR (use IN (...))."

    # Garante LIMIT
    if not _LIMIT_RE.search(sql):
        sql = sql + f"\nLIMIT {_MAX_ROWS}"
        logger.debug("[SQLGenerator] LIMIT ausente — adicionado automaticamente")

    # Garante WHERE cliente = '...'
    if not _CLIENTE_RE.search(sql):
        inject = f"cliente = '{cliente}'"
        if re.search(r"\bWHERE\b", sql, re.IGNORECASE):
            sql = re.sub(r"(\bWHERE\b)", rf"\1 {inject} AND ", sql, count=1, flags=re.IGNORECASE)
        else:
            sql = re.sub(
                r"(\b(GROUP\s+BY|ORDER\s+BY|LIMIT)\b)",
                f"WHERE {inject}\n\\1",
                sql,
                count=1,
                flags=re.IGNORECASE,
            )
            if not _CLIENTE_RE.search(sql):
                sql = sql + f"\nWHERE {inject}"
        logger.warning(
            "[SQLGenerator] WHERE cliente ausente — injetado: cliente='%s'", cliente
        )

    return sql, None


# ── Ponto de entrada principal ────────────────────────────────────────────────

def gerar_sql(
    pergunta: str,
    cliente_key: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> str:
    """
    Gera um SELECT BigQuery para a pergunta informada.

    Usa Query Planner para perguntas complexas (comparativos, multi-filtro),
    e modo direto para perguntas simples.

    Returns:
        SQL validado e pronto para execução no BigQuery via query_fed().

    Raises:
        ValueError: se o SQL gerado não passar na validação.
        RuntimeError: se o LLM falhar.
    """
    cliente = cliente_key or "source_of"
    complexa = _is_complex_query(pergunta)
    logger.info(
        "[SQLGenerator] Modo=%s para: %r",
        "planner" if complexa else "direto",
        pergunta[:60],
    )

    sql_raw = None

    if complexa:
        try:
            plan = _run_planner(pergunta, cliente, data_inicio, data_fim)
            sql_raw = _sql_from_plan(plan, pergunta, cliente)
        except ValueError as e:
            logger.warning("[QueryPlanner] Falhou, caindo no modo direto: %s", e)
            complexa = False

    if not complexa:
        periodo_ctx = ""
        if data_inicio or data_fim:
            periodo_ctx = f"\nPeríodo solicitado: {data_inicio or '?'} até {data_fim or 'hoje'}"

        user_msg = (
            f"Cliente: {cliente}{periodo_ctx}\n"
            f"Pergunta: {pergunta}\n\n"
            "Gere o SQL para responder essa pergunta. Apenas o SELECT ou WITH, nada mais."
        )
        sql_raw = _call_llm(system=_SQL_GEN_SYSTEM, user=user_msg, max_tokens=512)

    sql_clean, err = _validate_sql(sql_raw, cliente)
    if err:
        logger.error("[SQLGenerator] Validação bloqueou: %s | sql=%r", err, sql_raw[:200])
        raise ValueError(f"SQL inválido: {err}")

    logger.info("[SQLGenerator] SQL final (%s):\n%s", "planner" if complexa else "direto", sql_clean)
    return sql_clean


def executar_sql_dinamico(
    pergunta: str,
    cliente_key: Optional[str] = None,
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> dict:
    """
    Gera e executa SQL dinâmico para uma pergunta não coberta pelos intents fixos.
    Usa BigQuery como fonte via query_fed() (com fallback DuckDB/SQLite).

    Returns:
        {
            "dados":      list[dict],
            "sql":        str,
            "erro":       str | None,
            "latencia_ms": int,
            "modo":       "planner" | "direto",
        }
    """
    t0 = time.time()
    modo = "planner" if _is_complex_query(pergunta) else "direto"

    try:
        sql = gerar_sql(pergunta, cliente_key, data_inicio, data_fim)
    except (ValueError, RuntimeError) as e:
        return {
            "dados": [],
            "sql": "",
            "erro": f"Não consegui gerar uma consulta para essa pergunta: {e}",
            "latencia_ms": int((time.time() - t0) * 1000),
            "modo": modo,
        }

    try:
        rows = _query_fed(sql, estrito=True)  # antes: kwarg inexistente → TypeError em toda pergunta
        logger.info(
            "[SQLGenerator] OK — %d linhas em %dms (modo=%s)",
            len(rows), int((time.time() - t0) * 1000), modo,
        )
        return {
            "dados": rows,
            "sql": sql,
            "erro": None,
            "latencia_ms": int((time.time() - t0) * 1000),
            "modo": modo,
        }
    except Exception as e:
        err_msg = str(e)
        logger.error("[SQLGenerator] Erro ao executar: %s | sql=%r", err_msg, sql[:200])

        # Mensagem amigável para erros de coluna
        if "not found" in err_msg.lower() or "unrecognized" in err_msg.lower():
            col = re.search(r"Name (\w+) not found", err_msg)
            col_name = col.group(1) if col else err_msg[:80]
            return {
                "dados": [],
                "sql": sql,
                "erro": f"Coluna `{col_name}` não existe na tabela. Tente reformular a pergunta.",
                "latencia_ms": int((time.time() - t0) * 1000),
                "modo": modo,
            }
        return {
            "dados": [],
            "sql": sql,
            "erro": f"Erro ao executar consulta: {err_msg[:200]}",
            "latencia_ms": int((time.time() - t0) * 1000),
            "modo": modo,
        }
