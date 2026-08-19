"""
agent/sql_generator.py — Geração dinâmica de SQL via LLM (Text-to-SQL).

Usado como fallback quando nenhum intent cobre a pergunta do usuário.

Fluxo simples (perguntas diretas):
  pergunta → [LLM: Generator] → SELECT validado → executa

Fluxo com Query Planner (perguntas complexas):
  pergunta → [LLM: Planner] → QueryPlan (JSON) → [LLM: Generator] → SELECT validado → executa

O Planner é ativado automaticamente por heurística quando a pergunta contém
palavras-chave de comparação, multi-filtro ou múltiplas dimensões.

Guardrails:
  - Apenas SELECT (bloqueia DDL/DML)
  - LIMIT máx 50 (forçado se ausente)
  - WHERE cliente_key obrigatório (isolamento entre clientes)
  - Timeout de 8s na execução
  - Colunas inexistentes → SQLite lança exceção → capturada com mensagem amigável
"""

import json
import logging
import re
import sqlite3
import time
from typing import Optional

import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

from db.connections import LEGACY_DB_PATH as DB_PATH

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _call_llm(system: str, user: str, max_tokens: int = 512) -> str:
    """Chamada simplificada ao Claude para geração de SQL."""
    client = _get_client()
    resp = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text.strip()


# ── Schema injetado no prompt (apenas campanhas_unificada — fonte canônica) ──
_SCHEMA_CONTEXT = """
Banco de dados SQLite com a seguinte view principal:

VIEW: campanhas_unificada
Colunas disponíveis:
  data_inicio       TEXT    — data de início do período (ex: '2026-08-11')
  data_fim          TEXT    — data de fim do período (ex: '2026-08-17')
  campanha          TEXT    — nome da campanha
  cliente_key       TEXT    — identificador do cliente: 'source_of' ou 'pod_kombucha'
  cliente_nome      TEXT    — nome legível do cliente
  plataforma        TEXT    — 'meta_ads' ou 'google_ads'
  investimento      REAL    — gasto total em R$
  valor_compras     REAL    — receita gerada em R$
  compras           REAL    — número de compras/conversões
  impressoes        REAL    — impressões totais
  cliques_link      REAL    — cliques no link
  cliques_todos     REAL    — todos os cliques (inclusive engajamento)
  alcance           REAL    — pessoas únicas alcançadas
  frequencia        REAL    — impressoes / alcance
  cpm               REAL    — custo por mil impressões
  ctr_link          REAL    — taxa de clique no link (cliques_link/impressoes*100)
  cpc_link          REAL    — custo por clique no link
  cpa               REAL    — custo por aquisição (investimento/compras)
  roas_compras      REAL    — ROAS bruto da plataforma (valor_compras/investimento)
  video_3s          REAL    — visualizações de vídeo de 3 segundos
  thruplays         REAL    — visualizações completas de vídeo
  reproducoes_video REAL    — reproduções de vídeo iniciadas
  visualizacoes_landing REAL — visualizações da landing page
  adicoes_carrinho  REAL    — adições ao carrinho
  checkout_iniciado REAL    — checkouts iniciados
  compras_app       REAL    — compras via app
  compras_site      REAL    — compras via site
  conversas_iniciadas REAL  — conversas iniciadas (WhatsApp/Messenger)

REGRAS OBRIGATÓRIAS:
  1. SEMPRE incluir WHERE cliente_key = '<valor>' para isolar o cliente
  2. SEMPRE incluir LIMIT (máximo 50)
  3. ROAS calculado como: ROUND(SUM(valor_compras)/NULLIF(SUM(investimento),0), 2)
     — nunca use AVG(roas_compras)
  4. CPA calculado como: ROUND(SUM(investimento)/NULLIF(SUM(compras),0), 2)
  5. CTR como: ROUND(SUM(cliques_link)/NULLIF(SUM(impressoes),0)*100, 2)
  6. Datas no formato ISO: '2026-08-01'
  7. Apenas SELECT — nenhum INSERT/UPDATE/DELETE/DROP
"""

# ── Prompt de geração de SQL (modo direto) ────────────────────────────────────
_SQL_GEN_SYSTEM = (
    "Você é um especialista em SQL para análise de campanhas de mídia paga.\n"
    "Gere APENAS o SQL — sem explicações, sem markdown, sem bloco de código.\n"
    "Responda somente com a query SQL pura, começando em SELECT.\n\n"
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
- Para ROAS use coluna "roas" (será calculado como SUM(valor_compras)/SUM(investimento))
- Para CPA use coluna "cpa" (será calculado como SUM(investimento)/SUM(compras))
- Nunca invente colunas fora do schema
- Se período não mencionado, deixe inicio/fim null
- Se não há comparação, deixe periodo_b null
"""

# ── Prompt do Generator (modo planner) ───────────────────────────────────────
_SQL_FROM_PLAN_SYSTEM = (
    "Você é um especialista em SQL para análise de campanhas de mídia paga.\n"
    "Receberá um plano de consulta em JSON e deve gerar APENAS o SQL correspondente.\n"
    "Sem explicações, sem markdown, sem bloco de código — apenas o SELECT.\n\n"
    + _SCHEMA_CONTEXT
    + """
INSTRUÇÕES PARA COMPARATIVO TEMPORAL:
  - Use dois CTEs (periodo_a, periodo_b) com os respectivos filtros de data
  - Faça JOIN pelos campos de granularidade
  - Calcule delta absoluto e percentual para cada métrica
  - Exemplo:
    WITH periodo_a AS (
      SELECT campanha, SUM(investimento) AS inv_a, SUM(valor_compras) AS rec_a
      FROM campanhas_unificada
      WHERE cliente_key = 'source_of' AND data_inicio >= '2026-08-11' AND data_fim <= '2026-08-17'
      GROUP BY campanha
    ), periodo_b AS (
      SELECT campanha, SUM(investimento) AS inv_b, SUM(valor_compras) AS rec_b
      FROM campanhas_unificada
      WHERE cliente_key = 'source_of' AND data_inicio >= '2026-08-04' AND data_fim <= '2026-08-10'
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
    cliente_key: str,
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
        f"Cliente: {cliente_key}{periodo_ctx}\n"
        f"Pergunta: {pergunta}\n\n"
        "Produza o QueryPlan JSON conforme o schema."
    )

    logger.info("[QueryPlanner] Planejando: %r", pergunta[:80])
    raw = _call_llm(system=_PLANNER_SYSTEM, user=user_msg, max_tokens=512)

    # Extrai JSON mesmo se vier com texto ao redor
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
    cliente_key: str,
) -> str:
    """
    Chama o LLM Generator com o QueryPlan serializado para gerar o SQL final.
    """
    user_msg = (
        f"Cliente: {cliente_key}\n"
        f"Pergunta original: {pergunta_original}\n\n"
        f"QueryPlan:\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
        "Gere o SQL correspondente. Apenas o SELECT, nada mais."
    )

    logger.info("[QueryPlanner] Gerando SQL a partir do plano")
    return _call_llm(system=_SQL_FROM_PLAN_SYSTEM, user=user_msg, max_tokens=700)


# ── Validação ─────────────────────────────────────────────────────────────────
_MAX_ROWS = 50
_SELECT_RE    = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|PRAGMA|VACUUM)\b",
    re.IGNORECASE,
)
_LIMIT_RE  = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_CLKEY_RE  = re.compile(r"cliente_key\s*=", re.IGNORECASE)


def _validate_sql(sql: str, cliente_key: str) -> tuple[str, str | None]:
    """
    Valida e normaliza o SQL gerado pelo LLM.
    Retorna (sql_limpo, erro_ou_None).
    """
    sql = sql.strip().rstrip(";")

    # Remove backticks finais ou iniciais (markdown leak)
    sql = re.sub(r"^```\w*\s*", "", sql)
    sql = re.sub(r"```\s*$", "", sql).strip()

    # SQLite não suporta FULL OUTER JOIN — converte para LEFT JOIN
    sql = re.sub(r"\bFULL\s+OUTER\s+JOIN\b", "LEFT JOIN", sql, flags=re.IGNORECASE)

    match = re.search(r"((?:WITH\b|SELECT\b).*)", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return sql, "SQL gerado não contém SELECT."
    sql = match.group(1).strip()

    if _FORBIDDEN_RE.search(sql):
        return sql, "SQL gerado contém operação não permitida (DDL/DML)."

    if not _LIMIT_RE.search(sql):
        sql = sql + f"\nLIMIT {_MAX_ROWS}"
        logger.debug("[SQLGenerator] LIMIT ausente — adicionado automaticamente")

    if not _CLKEY_RE.search(sql):
        inject = f"cliente_key = '{cliente_key}'"
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
            if not _CLKEY_RE.search(sql):
                sql = sql + f"\nWHERE {inject}"
        logger.warning(
            "[SQLGenerator] WHERE cliente_key ausente — injetado: cliente_key='%s'", cliente_key
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
    Gera um SELECT para a pergunta informada.

    Usa Query Planner para perguntas complexas (comparativos, multi-filtro),
    e modo direto para perguntas simples.

    Returns:
        SQL validado e pronto para execução.

    Raises:
        ValueError: se o SQL gerado não passar na validação.
        RuntimeError: se o LLM falhar.
    """
    complexa = _is_complex_query(pergunta)
    logger.info(
        "[SQLGenerator] Modo=%s para: %r",
        "planner" if complexa else "direto",
        pergunta[:60],
    )

    if complexa:
        # Caminho com Planner: 2 chamadas LLM
        try:
            plan = _run_planner(pergunta, cliente_key, data_inicio, data_fim)
            sql_raw = _sql_from_plan(plan, pergunta, cliente_key)
        except ValueError as e:
            logger.warning("[QueryPlanner] Falhou, caindo no modo direto: %s", e)
            complexa = False  # fallback para modo direto abaixo

    if not complexa:
        # Caminho direto: 1 chamada LLM
        periodo_ctx = ""
        if data_inicio or data_fim:
            periodo_ctx = f"\nPeríodo solicitado: {data_inicio or '?'} até {data_fim or 'hoje'}"

        user_msg = (
            f"Cliente: {cliente_key}{periodo_ctx}\n"
            f"Pergunta: {pergunta}\n\n"
            "Gere o SQL para responder essa pergunta. Apenas o SELECT, nada mais."
        )
        sql_raw = _call_llm(system=_SQL_GEN_SYSTEM, user=user_msg, max_tokens=512)

    # Validação compartilhada para ambos os modos
    sql_clean, err = _validate_sql(sql_raw, cliente_key)
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
        conn = sqlite3.connect(DB_PATH, timeout=8)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
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
    except sqlite3.OperationalError as e:
        err_msg = str(e)
        logger.error("[SQLGenerator] Erro SQLite: %s | sql=%r", err_msg, sql[:200])
        if "no such column" in err_msg:
            col = re.search(r"no such column: (\S+)", err_msg)
            col_name = col.group(1) if col else err_msg
            return {
                "dados": [],
                "sql": sql,
                "erro": f"Coluna `{col_name}` não existe. Tente reformular a pergunta.",
                "latencia_ms": int((time.time() - t0) * 1000),
                "modo": modo,
            }
        return {
            "dados": [],
            "sql": sql,
            "erro": f"Erro ao executar consulta: {err_msg}",
            "latencia_ms": int((time.time() - t0) * 1000),
            "modo": modo,
        }
    except Exception as e:
        logger.error("[SQLGenerator] Erro inesperado: %s", e, exc_info=True)
        return {
            "dados": [],
            "sql": "",
            "erro": f"Erro inesperado: {e}",
            "latencia_ms": int((time.time() - t0) * 1000),
            "modo": modo,
        }
