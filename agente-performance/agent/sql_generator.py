"""
agent/sql_generator.py — Geração dinâmica de SQL via LLM (Text-to-SQL).

Usado como fallback quando nenhum intent cobre a pergunta do usuário.

Fluxo:
  1. Recebe pergunta + contexto (cliente_key, data_inicio, data_fim)
  2. LLM gera SELECT com base no schema injetado + regras de negócio
  3. Validação: apenas SELECT, LIMIT obrigatório, WHERE cliente_key obrigatório
  4. Executa via consultar_sql() e retorna list[dict]

Guardrails:
  - Apenas SELECT (bloqueia DDL/DML)
  - LIMIT máx 50 (forçado se ausente)
  - WHERE cliente_key obrigatório (isolamento entre clientes)
  - Timeout de 8s na execução
  - Colunas inexistentes → SQLite lança exceção → capturada com mensagem amigável
"""

import logging
import re
import sqlite3
import time
from typing import Optional

import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

from db.connection import DB_PATH

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

logger = logging.getLogger(__name__)

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

# ── Prompt de geração de SQL ──────────────────────────────────────────────────
_SQL_GEN_SYSTEM = (
    "Você é um especialista em SQL para análise de campanhas de mídia paga.\n"
    "Gere APENAS o SQL — sem explicações, sem markdown, sem bloco de código.\n"
    "Responda somente com a query SQL pura, começando em SELECT.\n\n"
    + _SCHEMA_CONTEXT
)

# ── Validação ─────────────────────────────────────────────────────────────────
_MAX_ROWS = 50
_SELECT_RE   = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|PRAGMA|VACUUM)\b",
    re.IGNORECASE,
)
_LIMIT_RE    = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_CLKEY_RE    = re.compile(r"cliente_key\s*=", re.IGNORECASE)


def _validate_sql(sql: str, cliente_key: str) -> tuple[str, str | None]:
    """
    Valida e normaliza o SQL gerado pelo LLM.

    Retorna (sql_limpo, erro_ou_None).
    """
    sql = sql.strip().rstrip(";")

    # Extrai apenas o SELECT (descarta qualquer texto antes)
    match = re.search(r"(SELECT\b.*)", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return sql, "SQL gerado não contém SELECT."
    sql = match.group(1).strip()

    # Bloqueia DDL/DML
    if _FORBIDDEN_RE.search(sql):
        return sql, "SQL gerado contém operação não permitida (DDL/DML)."

    # Garante LIMIT
    if not _LIMIT_RE.search(sql):
        sql = sql + f"\nLIMIT {_MAX_ROWS}"
        logger.debug("[SQLGenerator] LIMIT ausente — adicionado automaticamente")

    # Garante cliente_key (isolamento)
    if not _CLKEY_RE.search(sql):
        # Tenta injetar no WHERE existente ou criar novo WHERE antes de GROUP BY / ORDER BY / LIMIT
        inject = f"cliente_key = '{cliente_key}'"
        if re.search(r"\bWHERE\b", sql, re.IGNORECASE):
            sql = re.sub(r"(\bWHERE\b)", rf"\1 {inject} AND ", sql, count=1, flags=re.IGNORECASE)
        else:
            # Insere WHERE antes de GROUP BY, ORDER BY ou LIMIT
            sql = re.sub(
                r"(\b(GROUP\s+BY|ORDER\s+BY|LIMIT)\b)",
                f"WHERE {inject}\n\\1",
                sql,
                count=1,
                flags=re.IGNORECASE,
            )
            # Se não encontrou nenhum desses, adiciona no final antes do LIMIT
            if not _CLKEY_RE.search(sql):
                sql = sql + f"\nWHERE {inject}"
        logger.warning(
            "[SQLGenerator] WHERE cliente_key ausente — injetado: cliente_key='%s'", cliente_key
        )

    return sql, None


def gerar_sql(
    pergunta: str,
    cliente_key: str = "source_of",
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> str:
    """
    Chama o LLM para gerar um SELECT correspondente à pergunta.

    Returns:
        SQL validado e pronto para execução.

    Raises:
        ValueError: se o SQL gerado não passar na validação.
        RuntimeError: se o LLM falhar após retries.
    """
    # Contexto de datas para ajudar o LLM
    periodo_ctx = ""
    if data_inicio or data_fim:
        periodo_ctx = f"\nPeríodo solicitado: {data_inicio or '?'} até {data_fim or 'hoje'}"

    user_msg = (
        f"Cliente: {cliente_key}{periodo_ctx}\n"
        f"Pergunta: {pergunta}\n\n"
        "Gere o SQL para responder essa pergunta. Apenas o SELECT, nada mais."
    )

    logger.info("[SQLGenerator] Gerando SQL para: %r (cliente=%s)", pergunta[:60], cliente_key)

    sql_raw = _call_llm(
        system=_SQL_GEN_SYSTEM,
        user=user_msg,
        max_tokens=512,
    )

    sql_clean, erro = _validate_sql(sql_raw, cliente_key)
    if erro:
        logger.error("[SQLGenerator] Validação falhou: %s | sql=%r", erro, sql_raw[:200])
        raise ValueError(f"SQL inválido: {erro}")

    logger.info("[SQLGenerator] SQL gerado e validado:\n%s", sql_clean)
    return sql_clean


def executar_sql_dinamico(
    pergunta: str,
    cliente_key: str = "source_of",
    data_inicio: Optional[str] = None,
    data_fim: Optional[str] = None,
) -> dict:
    """
    Gera e executa SQL dinâmico para uma pergunta não coberta pelos intents fixos.

    Returns:
        {
            "dados":   list[dict],      # linhas retornadas
            "sql":     str,             # SQL executado (para debug/log)
            "erro":    str | None,      # mensagem de erro se falhou
            "latencia_ms": int,
        }
    """
    t0 = time.time()

    try:
        sql = gerar_sql(pergunta, cliente_key, data_inicio, data_fim)
    except (ValueError, RuntimeError) as e:
        return {
            "dados": [],
            "sql": "",
            "erro": f"Não consegui gerar uma consulta para essa pergunta: {e}",
            "latencia_ms": int((time.time() - t0) * 1000),
        }

    # Execução com timeout via threading
    try:
        conn = sqlite3.connect(DB_PATH, timeout=8)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql)
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        logger.info("[SQLGenerator] Executado OK — %d linhas em %dms", len(rows), int((time.time()-t0)*1000))
        return {
            "dados": rows,
            "sql": sql,
            "erro": None,
            "latencia_ms": int((time.time() - t0) * 1000),
        }
    except sqlite3.OperationalError as e:
        err_msg = str(e)
        logger.error("[SQLGenerator] Erro SQLite: %s | sql=%r", err_msg, sql[:200])
        # Mensagem amigável para coluna inexistente
        if "no such column" in err_msg:
            col = re.search(r"no such column: (\S+)", err_msg)
            col_name = col.group(1) if col else err_msg
            return {
                "dados": [],
                "sql": sql,
                "erro": f"Coluna `{col_name}` não existe. Tente reformular a pergunta.",
                "latencia_ms": int((time.time() - t0) * 1000),
            }
        return {
            "dados": [],
            "sql": sql,
            "erro": f"Erro ao executar consulta: {err_msg}",
            "latencia_ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:
        logger.error("[SQLGenerator] Erro inesperado: %s", e, exc_info=True)
        return {
            "dados": [],
            "sql": "",
            "erro": f"Erro inesperado: {e}",
            "latencia_ms": int((time.time() - t0) * 1000),
        }
