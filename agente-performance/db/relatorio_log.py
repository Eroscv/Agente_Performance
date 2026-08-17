"""
db/relatorio_log.py

Registra todos os relatórios gerados (diários, semanais, mensais) no SQLite.
Tabela: relatorios_log
Schema separado por cliente, plataforma e periodicidade.

Uso:
    from db.relatorio_log import registrar_relatorio, listar_relatorios, ultimo_relatorio
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS relatorios_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente_key     TEXT NOT NULL,
    plataforma      TEXT NOT NULL,
    periodicidade   TEXT NOT NULL,
    data_inicio     TEXT NOT NULL,
    data_fim        TEXT NOT NULL,
    titulo          TEXT,
    notion_url      TEXT,
    notion_page_id  TEXT,
    slack_ok        INTEGER DEFAULT 0,
    slack_ts        TEXT,
    blocos_n        INTEGER DEFAULT 0,
    invest_total    REAL,
    receita_total   REAL,
    roas            REAL,
    conversoes      INTEGER,
    status          TEXT DEFAULT 'ok',
    erro_msg        TEXT,
    gerado_em       TEXT DEFAULT (datetime('now')),
    duracao_ms      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_rellog_cliente     ON relatorios_log(cliente_key);
CREATE INDEX IF NOT EXISTS idx_rellog_periodo     ON relatorios_log(periodicidade);
CREATE INDEX IF NOT EXISTS idx_rellog_data        ON relatorios_log(data_inicio, data_fim);
CREATE INDEX IF NOT EXISTS idx_rellog_cliente_per ON relatorios_log(cliente_key, periodicidade);
"""


def _conn() -> sqlite3.Connection:
    """Abre conexão dedicada (com WAL) e garante que a tabela existe."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(CREATE_TABLE)
    return conn


def registrar_relatorio(
    cliente_key: str,
    plataforma: str,
    periodicidade: str,
    data_inicio: str,
    data_fim: str,
    titulo: str = None,
    notion_url: str = None,
    notion_page_id: str = None,
    slack_ok: bool = False,
    slack_ts: str = None,
    blocos_n: int = 0,
    resumo: dict = None,
    status: str = "ok",
    erro_msg: str = None,
    duracao_ms: int = None,
) -> int:
    """
    Insere um registro de relatório gerado no banco.

    Args:
        cliente_key:    Chave do cliente ('source_of', 'pod_kombucha', ...)
        plataforma:     Plataforma de origem ('meta_ads', 'google_ads', 'shopify', 'consolidado')
        periodicidade:  Frequência ('diario', 'semanal', 'mensal', 'periodo')
        data_inicio:    Data início do período (YYYY-MM-DD)
        data_fim:       Data fim do período (YYYY-MM-DD)
        titulo:         Título do relatório (ex: nome da página no Notion)
        notion_url:     URL pública da página no Notion
        notion_page_id: ID da página no Notion
        slack_ok:       True se postou no Slack com sucesso
        slack_ts:       Timestamp da mensagem no Slack
        blocos_n:       Número de blocos Notion gerados
        resumo:         Dict com métricas (invest_total, receita_total, roas, conversoes)
        status:         'ok' | 'erro' | 'sem_dados'
        erro_msg:       Mensagem de erro, quando aplicável
        duracao_ms:     Tempo de geração em milissegundos

    Returns:
        ID do registro inserido.
    """
    # Extrai métricas do dict resumo, se fornecido
    invest_total  = None
    receita_total = None
    roas_val      = None
    conversoes    = None

    if resumo and isinstance(resumo, dict):
        invest_total  = _to_float(resumo.get("investimento") or resumo.get("invest_total") or resumo.get("total_invest"))
        receita_total = _to_float(resumo.get("receita") or resumo.get("receita_total") or resumo.get("receita_shopify"))
        roas_val      = _to_float(resumo.get("roas"))
        conversoes    = _to_int(resumo.get("conversoes") or resumo.get("compras"))

    conn = _conn()
    try:
        cur = conn.execute("""
            INSERT INTO relatorios_log (
                cliente_key, plataforma, periodicidade,
                data_inicio, data_fim,
                titulo, notion_url, notion_page_id,
                slack_ok, slack_ts,
                blocos_n,
                invest_total, receita_total, roas, conversoes,
                status, erro_msg, duracao_ms
            ) VALUES (
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
        """, (
            cliente_key, plataforma, periodicidade,
            str(data_inicio), str(data_fim),
            titulo, notion_url, notion_page_id,
            int(bool(slack_ok)), slack_ts,
            blocos_n or 0,
            invest_total, receita_total, roas_val, conversoes,
            status, erro_msg, duracao_ms,
        ))
        conn.commit()
        new_id = cur.lastrowid
        logger.debug(
            "[RelatorioLog] Registrado id=%d: %s/%s/%s (%s→%s) status=%s",
            new_id, cliente_key, plataforma, periodicidade, data_inicio, data_fim, status,
        )
        return new_id
    finally:
        conn.close()


def listar_relatorios(
    cliente_key: str = None,
    periodicidade: str = None,
    limit: int = 50,
) -> list[dict]:
    """
    Lista relatórios registrados com filtros opcionais.

    Args:
        cliente_key:   Filtra por cliente (None = todos)
        periodicidade: Filtra por periodicidade (None = todas)
        limit:         Máximo de registros retornados

    Returns:
        Lista de dicts com os campos da tabela relatorios_log.
    """
    conn = _conn()
    try:
        where  = []
        params = []

        if cliente_key:
            where.append("cliente_key = ?")
            params.append(cliente_key)
        if periodicidade:
            where.append("periodicidade = ?")
            params.append(periodicidade)

        sql = "SELECT * FROM relatorios_log"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY gerado_em DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def ultimo_relatorio(
    cliente_key: str,
    periodicidade: str,
) -> Optional[dict]:
    """
    Retorna o último relatório registrado para um cliente + periodicidade.

    Args:
        cliente_key:   Chave do cliente
        periodicidade: Periodicidade ('diario', 'semanal', 'mensal', ...)

    Returns:
        Dict com os campos do registro, ou None se não existir.
    """
    conn = _conn()
    try:
        row = conn.execute("""
            SELECT * FROM relatorios_log
            WHERE cliente_key = ? AND periodicidade = ?
            ORDER BY gerado_em DESC
            LIMIT 1
        """, (cliente_key, periodicidade)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── Helpers internos ───────────────────────────────────────────────────────────

def _to_float(v) -> Optional[float]:
    """Converte valor para float, retorna None se inválido."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v) -> Optional[int]:
    """Converte valor para int, retorna None se inválido."""
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
