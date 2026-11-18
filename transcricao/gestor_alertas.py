"""
gestor_alertas.py

Sistema de tickets com estado explícito para alertas de reuniões.

Ciclo de vida de um ticket:
  ABERTO    → alerta detectado numa reunião
  RESOLVIDO → Claude confirma resolução em reunião posterior
  FECHADO   → encerrado manualmente (Slack ✅) ou resolvido há >7 dias

Critério de resolução (ponto 5):
  NÃO é "qualquer mensagem no grupo".
  É: Claude analisa a reunião seguinte e confirma explicitamente
  que o item foi tratado, entregue ou descartado — com evidência textual.

Uso:
  python3 gestor_alertas.py --processar   # abre tickets novos + fecha resolvidos
  python3 gestor_alertas.py --status      # lista tickets abertos
  python3 gestor_alertas.py --fechar <id> # fecha manualmente
  python3 gestor_alertas.py --dry-run     # sem salvar / sem Slack
"""

import argparse
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv
from google.cloud import bigquery as bq
from google.oauth2 import service_account as _sa

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
AGENTE_DIR = BASE_DIR.parent / "agente-performance"
ENV_FILE   = AGENTE_DIR / ".env"
DB_PATH    = BASE_DIR / "db" / "reunioes.db"
BQ_CREDS   = BASE_DIR / "credentials" / "gdrive-service-account.json"

load_dotenv(ENV_FILE)

ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL        = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
SLACK_BOT_TOKEN     = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_ALERT_CHANNEL = os.getenv("SLACK_ALERT_CHANNEL", "performance")
BQ_PROJECT          = "guara-db"
BQ_DATASET          = "guara_performance"
BQ_TABLE_TICKETS    = "tickets_alerta"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── SQLite — schema de tickets ────────────────────────────────────────────────

DDL_TICKETS = """
CREATE TABLE IF NOT EXISTS tickets_alerta (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id        TEXT UNIQUE NOT NULL,   -- hash estável: fonte_id + índice do alerta
    fonte_id         TEXT NOT NULL,          -- reunião de origem
    reuniao_titulo   TEXT,
    reuniao_data     TEXT,
    tipo             TEXT NOT NULL,          -- sem_resposta | urgente | sem_acesso | bloqueio | conflito
    cliente          TEXT,
    descricao        TEXT,
    trecho           TEXT,
    status           TEXT NOT NULL DEFAULT 'aberto',   -- aberto | resolvido | fechado
    -- resolução
    resolvido_em     TEXT,
    resolvido_por    TEXT,                   -- "reuniao:<fonte_id>" | "manual:<user>"
    evidencia        TEXT,                   -- trecho da reunião que confirma resolução
    -- controle
    aberto_em        TEXT NOT NULL,
    atualizado_em    TEXT NOT NULL,
    notificado_slack INTEGER DEFAULT 0       -- 1 se alerta de abertura foi enviado
);
"""

DDL_TICKETS_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets_alerta(status)",
    "CREATE INDEX IF NOT EXISTS idx_tickets_fonte  ON tickets_alerta(fonte_id)",
    "CREATE INDEX IF NOT EXISTS idx_tickets_tipo   ON tickets_alerta(tipo)",
]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(DDL_TICKETS)
    for idx in DDL_TICKETS_IDX:
        conn.execute(idx)
    conn.commit()
    return conn


def ticket_id(fonte_id: str, idx: int) -> str:
    import hashlib
    raw = f"{fonte_id}:{idx}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ── BigQuery — tabela tickets_alerta ─────────────────────────────────────────

BQ_SCHEMA_TICKETS = [
    bq.SchemaField("ticket_id",      "STRING",    mode="REQUIRED"),
    bq.SchemaField("fonte_id",       "STRING",    mode="REQUIRED"),
    bq.SchemaField("reuniao_titulo", "STRING",    mode="NULLABLE"),
    bq.SchemaField("reuniao_data",   "DATE",      mode="NULLABLE"),
    bq.SchemaField("tipo",           "STRING",    mode="REQUIRED"),
    bq.SchemaField("cliente",        "STRING",    mode="NULLABLE"),
    bq.SchemaField("descricao",      "STRING",    mode="NULLABLE"),
    bq.SchemaField("trecho",         "STRING",    mode="NULLABLE"),
    bq.SchemaField("status",         "STRING",    mode="REQUIRED"),
    bq.SchemaField("resolvido_em",   "TIMESTAMP", mode="NULLABLE"),
    bq.SchemaField("resolvido_por",  "STRING",    mode="NULLABLE"),
    bq.SchemaField("evidencia",      "STRING",    mode="NULLABLE"),
    bq.SchemaField("aberto_em",      "TIMESTAMP", mode="REQUIRED"),
    bq.SchemaField("atualizado_em",  "TIMESTAMP", mode="REQUIRED",
                   description="Timestamp desta versão — use v_tickets_alerta para deduplicar"),
]

_bq_client = None


def get_bq_client():
    global _bq_client
    if _bq_client is None:
        creds = _sa.Credentials.from_service_account_file(
            str(BQ_CREDS),
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        _bq_client = bq.Client(project=BQ_PROJECT, credentials=creds)
    return _bq_client


def get_bq_table_tickets():
    client = get_bq_client()
    dataset_ref = client.dataset(BQ_DATASET)
    table_ref   = dataset_ref.table(BQ_TABLE_TICKETS)
    try:
        return client.get_table(table_ref)
    except Exception:
        table = bq.Table(table_ref, schema=BQ_SCHEMA_TICKETS)
        t = client.create_table(table)
        logger.info("[BQ] Tabela '%s' criada.", BQ_TABLE_TICKETS)
        # Cria view deduplicada
        _criar_view_tickets(client)
        return client.get_table(table_ref)


def _criar_view_tickets(client):
    view_ref = client.dataset(BQ_DATASET).table("v_tickets_alerta")
    view = bq.Table(view_ref)
    view.view_query = """
        SELECT * EXCEPT(rn)
        FROM (
          SELECT *,
            ROW_NUMBER() OVER (
              PARTITION BY ticket_id
              ORDER BY atualizado_em DESC
            ) AS rn
          FROM `guara-db.guara_performance.tickets_alerta`
        )
        WHERE rn = 1
    """.strip()
    try:
        client.delete_table(f"{BQ_PROJECT}.{BQ_DATASET}.v_tickets_alerta")
    except Exception:
        pass
    client.create_table(view)
    logger.info("[BQ] View 'v_tickets_alerta' criada.")


def upsert_ticket_bq(ticket: sqlite3.Row):
    """Insere/atualiza ticket no BQ via append + atualizado_em."""
    try:
        table = get_bq_table_tickets()
        agora = datetime.now(timezone.utc).isoformat()
        row = {
            "ticket_id":      ticket["ticket_id"],
            "fonte_id":       ticket["fonte_id"],
            "reuniao_titulo": ticket["reuniao_titulo"],
            "reuniao_data":   ticket["reuniao_data"] or None,
            "tipo":           ticket["tipo"],
            "cliente":        ticket["cliente"],
            "descricao":      ticket["descricao"],
            "trecho":         ticket["trecho"],
            "status":         ticket["status"],
            "resolvido_em":   ticket["resolvido_em"] or None,
            "resolvido_por":  ticket["resolvido_por"] or None,
            "evidencia":      ticket["evidencia"] or None,
            "aberto_em":      ticket["aberto_em"],
            "atualizado_em":  agora,
        }
        errors = get_bq_client().insert_rows_json(table, [row])
        if errors:
            logger.warning("[BQ] Erro ao inserir ticket %s: %s", ticket["ticket_id"], errors)
    except Exception as e:
        logger.warning("[BQ] Exceção ao inserir ticket: %s", e)


# ── Abertura de tickets ───────────────────────────────────────────────────────

TIPOS_CRITICOS = {"sem_resposta", "urgente", "sem_acesso", "bloqueio"}


def abrir_tickets_da_analise(conn: sqlite3.Connection, analise: sqlite3.Row, dry_run: bool) -> int:
    """Abre tickets para alertas críticos ainda não registrados. Retorna qtd aberta."""
    alertas = json.loads(analise["alertas"] or "[]")
    abertos = 0
    agora   = datetime.now(timezone.utc).isoformat()

    for i, alerta in enumerate(alertas):
        if alerta.get("tipo") not in TIPOS_CRITICOS:
            continue

        tid = ticket_id(analise["fonte_id"], i)

        # Já existe?
        existente = conn.execute(
            "SELECT id, status FROM tickets_alerta WHERE ticket_id = ?", (tid,)
        ).fetchone()
        if existente:
            continue

        if dry_run:
            logger.info(
                "  [DRY-RUN] Ticket %s | %s | %s — %s",
                tid, alerta["tipo"], alerta.get("cliente", "?"), alerta.get("descricao", "")
            )
            abertos += 1
            continue

        conn.execute("""
            INSERT INTO tickets_alerta
              (ticket_id, fonte_id, reuniao_titulo, reuniao_data,
               tipo, cliente, descricao, trecho,
               status, aberto_em, atualizado_em, notificado_slack)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,0)
        """, (
            tid,
            analise["fonte_id"],
            analise["titulo"],
            analise["data"],
            alerta["tipo"],
            alerta.get("cliente", ""),
            alerta.get("descricao", ""),
            alerta.get("trecho", ""),
            "aberto",
            agora,
            agora,
        ))
        conn.commit()

        ticket = conn.execute(
            "SELECT * FROM tickets_alerta WHERE ticket_id = ?", (tid,)
        ).fetchone()
        upsert_ticket_bq(ticket)
        abertos += 1

    return abertos


# ── Resolução via Claude ──────────────────────────────────────────────────────

PROMPT_RESOLUCAO = """Você é um auditor de pendências de uma agência de marketing.

Analise o texto abaixo (transcrição de uma reunião posterior) e determine se o item pendente foi resolvido.

ITEM PENDENTE:
- Tipo: {tipo}
- Cliente: {cliente}
- Descrição: {descricao}
- Trecho original: {trecho}

TEXTO DA REUNIÃO POSTERIOR:
{texto_reuniao}

Responda SOMENTE com JSON válido:
{{
  "resolvido": true | false,
  "confianca": "alta" | "media" | "baixa",
  "evidencia": "trecho exato da reunião que prova a resolução (máx 200 chars) ou null",
  "justificativa": "por que foi resolvido ou por que não foi (máx 100 chars)"
}}

Regras ESTRITAS:
- "resolvido": true APENAS se há menção explícita de que o item foi entregue, concluído, aprovado ou explicitamente descartado
- Um "bom dia" ou conversa casual NÃO resolve o item
- Menção ao tema sem conclusão = false
- Dúvida = false
- confianca "alta": prova textual direta | "media": forte indício | "baixa": inferência
"""


def verificar_resolucao_claude(ticket: sqlite3.Row, texto_reuniao: str) -> dict:
    prompt = PROMPT_RESOLUCAO.format(
        tipo=ticket["tipo"],
        cliente=ticket["cliente"] or "?",
        descricao=ticket["descricao"] or "",
        trecho=ticket["trecho"] or "",
        texto_reuniao=texto_reuniao[:3000],
    )
    from utils.llm import gerar
    raw = gerar("", prompt, max_tokens=512, temperature=0.0)
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def processar_resolucoes(conn: sqlite3.Connection, dry_run: bool) -> int:
    """
    Para cada ticket aberto, verifica nas reuniões POSTERIORES
    se há evidência explícita de resolução.
    """
    tickets_abertos = conn.execute("""
        SELECT t.*, r.data as r_data
        FROM tickets_alerta t
        JOIN reunioes r ON r.fonte_id = t.fonte_id
        WHERE t.status = 'aberto'
        ORDER BY t.aberto_em
    """).fetchall()

    if not tickets_abertos:
        return 0

    resolvidos = 0
    agora = datetime.now(timezone.utc).isoformat()

    for ticket in tickets_abertos:
        # Reuniões POSTERIORES à data do ticket
        reunioes_posteriores = conn.execute("""
            SELECT fonte_id, titulo, data, resumo, decisoes, proximas_etapas, detalhes
            FROM reunioes
            WHERE data > ?
            ORDER BY data ASC
            LIMIT 5
        """, (ticket["reuniao_data"] or "2000-01-01",)).fetchall()

        for reuniao in reunioes_posteriores:
            texto = "\n".join(filter(None, [
                f"Título: {reuniao['titulo']} ({reuniao['data']})",
                reuniao["resumo"] or "",
                reuniao["decisoes"] or "",
                reuniao["proximas_etapas"] or "",
                (reuniao["detalhes"] or "")[:2000],
            ]))

            try:
                resultado = verificar_resolucao_claude(ticket, texto)
            except Exception as e:
                logger.warning("  Claude falhou para ticket %s: %s", ticket["ticket_id"], e)
                continue

            if resultado.get("resolvido") and resultado.get("confianca") in ("alta", "media"):
                logger.info(
                    "  ✅ Resolvido [%s] %s — evidência: %s",
                    ticket["ticket_id"], ticket["descricao"][:60],
                    resultado.get("evidencia", "")[:80],
                )

                if not dry_run:
                    conn.execute("""
                        UPDATE tickets_alerta
                        SET status='resolvido',
                            resolvido_em=?,
                            resolvido_por=?,
                            evidencia=?,
                            atualizado_em=?
                        WHERE ticket_id=?
                    """, (
                        agora,
                        f"reuniao:{reuniao['fonte_id']}",
                        resultado.get("evidencia", ""),
                        agora,
                        ticket["ticket_id"],
                    ))
                    conn.commit()

                    t_atualizado = conn.execute(
                        "SELECT * FROM tickets_alerta WHERE ticket_id=?",
                        (ticket["ticket_id"],)
                    ).fetchone()
                    upsert_ticket_bq(t_atualizado)
                    enviar_slack_resolucao(ticket, resultado, reuniao)

                resolvidos += 1
                break  # para nesta reunião — já resolvido

    return resolvidos


# ── Slack ─────────────────────────────────────────────────────────────────────

EMOJI_TIPO = {
    "sem_resposta": "🔇",
    "urgente":      "🚨",
    "sem_acesso":   "🔒",
    "bloqueio":     "🚫",
    "conflito":     "⚠️",
}


def enviar_slack_resolucao(ticket: sqlite3.Row, resultado: dict, reuniao: sqlite3.Row):
    """Notifica no Slack que um ticket foi resolvido com evidência."""
    emoji = EMOJI_TIPO.get(ticket["tipo"], "⚠️")
    payload = {
        "channel": SLACK_ALERT_CHANNEL,
        "text":    f"Ticket resolvido: {ticket['descricao']}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"✅ Pendência resolvida"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tipo:* {emoji} `{ticket['tipo']}`"},
                    {"type": "mrkdwn", "text": f"*Cliente:* {ticket['cliente'] or '?'}"},
                    {"type": "mrkdwn", "text": f"*Pendência:* {ticket['descricao']}"},
                    {"type": "mrkdwn", "text": f"*Resolvida em:* {reuniao['titulo']} ({reuniao['data']})"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"📋 *Evidência:* _{resultado.get('evidencia', '')}_ (confiança: {resultado.get('confianca', '?')})"},
            },
        ],
    }
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    if not resp.json().get("ok"):
        logger.warning("[Slack] Falha ao notificar resolução: %s", resp.json().get("error"))


# ── Status ────────────────────────────────────────────────────────────────────

def exibir_status(conn: sqlite3.Connection):
    tickets = conn.execute("""
        SELECT ticket_id, tipo, cliente, descricao, status, reuniao_titulo, reuniao_data,
               aberto_em, resolvido_em, evidencia
        FROM tickets_alerta
        ORDER BY
          CASE status WHEN 'aberto' THEN 0 WHEN 'resolvido' THEN 1 ELSE 2 END,
          aberto_em DESC
    """).fetchall()

    contagem = {"aberto": 0, "resolvido": 0, "fechado": 0}
    for t in tickets:
        contagem[t["status"]] = contagem.get(t["status"], 0) + 1

    print(f"\n{'='*70}")
    print(f"  TICKETS — abertos: {contagem['aberto']} | resolvidos: {contagem['resolvido']} | fechados: {contagem['fechado']}")
    print(f"{'='*70}")

    for t in tickets:
        emoji = EMOJI_TIPO.get(t["tipo"], "⚠️")
        status_icon = {"aberto": "🔴", "resolvido": "✅", "fechado": "⬛"}.get(t["status"], "?")
        print(f"\n{status_icon} [{t['ticket_id']}] {emoji} {t['tipo'].upper()} | {t['cliente'] or '?'}")
        print(f"   {t['descricao'][:80]}")
        print(f"   Origem: {t['reuniao_titulo']} ({t['reuniao_data']})")
        if t["resolvido_em"]:
            print(f"   Resolvido: {t['resolvido_em'][:10]} | Evidência: {(t['evidencia'] or '')[:60]}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--processar", action="store_true", help="Abre novos tickets + verifica resoluções")
    parser.add_argument("--status",    action="store_true", help="Lista todos os tickets")
    parser.add_argument("--fechar",    metavar="TICKET_ID", help="Fecha ticket manualmente")
    parser.add_argument("--dry-run",   action="store_true", help="Sem salvar / sem Slack")
    args = parser.parse_args()

    # Orquestração delegada ao TicketAgent (Camada 3) — ver agents/ticket.py.
    # Import local: evita import circular (agents.ticket importa este módulo).
    from agents.ticket import TicketAgent
    agente = TicketAgent()

    if args.fechar:
        conn = get_conn()
        ok = agente.fechar_manual(conn, args.fechar)
        conn.close()
        if ok:
            logger.info("Ticket %s fechado manualmente.", args.fechar)
        else:
            logger.warning("Ticket %s não encontrado.", args.fechar)

    elif args.status:
        conn = get_conn()
        agente.status(conn)
        conn.close()

    elif args.processar:
        agente.executar(dry_run=args.dry_run)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
