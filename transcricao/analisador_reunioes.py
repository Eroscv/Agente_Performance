"""
analisador_reunioes.py

Analisa reuniões já sincronizadas no SQLite, detecta sinais críticos
via Claude e envia alertas no Slack.

Fluxo:
  1. Busca reuniões não analisadas no SQLite
  2. Envia o texto para Claude (Haiku) com prompt estruturado
  3. Salva resultado em SQLite + BigQuery (tabela: analise_reunioes)
  4. Dispara alerta no Slack se sinal crítico detectado

Sinais detectados:
  - sem_resposta  : cliente não retornou / sem feedback
  - urgente       : prazo, urgência, bloqueio explícito
  - sem_acesso    : falta de acesso / integração pendente
  - sentimento    : positivo / neutro / negativo

Uso:
  python3 analisador_reunioes.py           # analisa novas reuniões
  python3 analisador_reunioes.py --todos   # reanálisa todas
  python3 analisador_reunioes.py --dry-run # sem salvar / sem Slack
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import requests
from dotenv import load_dotenv
from google.cloud import bigquery as bq
from google.oauth2 import service_account as _sa

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
AGENTE_DIR  = BASE_DIR.parent / "agente-performance"
ENV_FILE    = AGENTE_DIR / ".env"
DB_PATH     = BASE_DIR / "db" / "reunioes.db"
BQ_CREDS    = BASE_DIR / "credentials" / "gdrive-service-account.json"

load_dotenv(ENV_FILE)

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL       = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
SLACK_BOT_TOKEN    = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_ALERT_CHANNEL = os.getenv("SLACK_ALERT_CHANNEL", "performance")

BQ_PROJECT = os.getenv("BQ_PROJECT", "guara-db")
BQ_DATASET = os.getenv("BQ_DATASET", "transcricao")
BQ_TABLE_ANALISE = "analise_reunioes"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────────

PROMPT_SISTEMA = """Você é um analista de reuniões de uma agência de marketing digital (Guará Media).
Analisa transcrições de reuniões e extrai:

1. **sentimento** geral da reunião: "positivo", "neutro" ou "negativo"
2. **alertas** — lista de sinais críticos encontrados no texto. Cada alerta tem:
   - tipo: "sem_resposta" | "urgente" | "sem_acesso" | "conflito" | "bloqueio"
   - cliente: nome do cliente mencionado (ou "interno" se for reunião interna)
   - descricao: frase curta descrevendo o problema (máx 100 chars)
   - trecho: trecho exato do texto que gerou o alerta (máx 200 chars)
3. **clientes_mencionados**: lista de nomes de clientes externos citados
4. **resumo_analise**: parágrafo curto (2-3 frases) com os pontos mais relevantes

Responda APENAS com JSON válido, sem markdown, sem explicações:
{
  "sentimento": "positivo|neutro|negativo",
  "alertas": [
    {
      "tipo": "sem_resposta|urgente|sem_acesso|conflito|bloqueio",
      "cliente": "nome do cliente ou interno",
      "descricao": "...",
      "trecho": "..."
    }
  ],
  "clientes_mencionados": ["cliente1", "cliente2"],
  "resumo_analise": "..."
}

Regras:
- Só inclua alertas reais — não invente problemas
- Se não houver alertas, retorne alertas: []
- Foque em problemas de clientes externos, não questões internas de processo
- "sem_resposta": cliente que não deu retorno, não aprovou, sumiu
- "urgente": deadline próximo, cliente pediu urgência, risco de perder conta
- "sem_acesso": falta de acesso a conta de anúncio, sistema, plataforma
- "bloqueio": algo impedindo a equipe de avançar num cliente
"""

# ── SQLite ────────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analise_reunioes (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            reuniao_id          INTEGER NOT NULL,
            fonte_id            TEXT NOT NULL,
            titulo              TEXT,
            data                TEXT,
            sentimento          TEXT,
            alertas             TEXT,  -- JSON
            clientes_mencionados TEXT, -- JSON
            resumo_analise      TEXT,
            analisado_em        TEXT NOT NULL,
            FOREIGN KEY (reuniao_id) REFERENCES reunioes(id)
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_analise_fonte_id
        ON analise_reunioes(fonte_id)
    """)
    conn.commit()
    return conn


def reunioes_pendentes(conn: sqlite3.Connection, todos: bool) -> list:
    if todos:
        return conn.execute("SELECT * FROM reunioes ORDER BY data").fetchall()
    return conn.execute("""
        SELECT r.* FROM reunioes r
        LEFT JOIN analise_reunioes a ON r.fonte_id = a.fonte_id
        WHERE a.id IS NULL
        ORDER BY r.data
    """).fetchall()


def salvar_analise_sqlite(conn: sqlite3.Connection, reuniao, resultado: dict):
    agora = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO analise_reunioes
          (reuniao_id, fonte_id, titulo, data, sentimento, alertas,
           clientes_mencionados, resumo_analise, analisado_em)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        reuniao["id"],
        reuniao["fonte_id"],
        reuniao["titulo"],
        reuniao["data"],
        resultado.get("sentimento", "neutro"),
        json.dumps(resultado.get("alertas", []), ensure_ascii=False),
        json.dumps(resultado.get("clientes_mencionados", []), ensure_ascii=False),
        resultado.get("resumo_analise", ""),
        agora,
    ))
    conn.commit()


# ── BigQuery ──────────────────────────────────────────────────────────────────

_bq_client = None

BQ_SCHEMA_ANALISE = [
    bq.SchemaField("fonte_id",             "STRING",    mode="REQUIRED"),
    bq.SchemaField("titulo",               "STRING",    mode="NULLABLE"),
    bq.SchemaField("data",                 "DATE",      mode="NULLABLE"),
    bq.SchemaField("sentimento",           "STRING",    mode="NULLABLE"),
    bq.SchemaField("alertas",              "STRING",    mode="NULLABLE"),  # JSON
    bq.SchemaField("clientes_mencionados", "STRING",    mode="NULLABLE"),  # JSON
    bq.SchemaField("resumo_analise",       "STRING",    mode="NULLABLE"),
    bq.SchemaField("analisado_em",         "TIMESTAMP", mode="NULLABLE"),
]


def get_bq_table_analise():
    global _bq_client
    try:
        if _bq_client is None:
            creds = _sa.Credentials.from_service_account_file(
                str(BQ_CREDS),
                scopes=["https://www.googleapis.com/auth/bigquery"],
            )
            _bq_client = bq.Client(project=BQ_PROJECT, credentials=creds)

        dataset_ref = _bq_client.dataset(BQ_DATASET)
        try:
            _bq_client.get_dataset(dataset_ref)
        except Exception:
            ds = bq.Dataset(dataset_ref)
            ds.location = "US"
            _bq_client.create_dataset(ds)

        table_ref = dataset_ref.table(BQ_TABLE_ANALISE)
        try:
            return _bq_client.get_table(table_ref)
        except Exception:
            table = bq.Table(table_ref, schema=BQ_SCHEMA_ANALISE)
            _bq_client.create_table(table)
            logger.info("[BQ] Tabela '%s' criada.", BQ_TABLE_ANALISE)
            return _bq_client.get_table(table_ref)
    except Exception as e:
        logger.warning("[BQ] Falha ao obter tabela analise: %s", e)
        return None


def salvar_analise_bq(reuniao, resultado: dict) -> bool:
    table = get_bq_table_analise()
    if table is None:
        return False
    try:
        row = {
            "fonte_id":             reuniao["fonte_id"],
            "titulo":               reuniao["titulo"],
            "data":                 reuniao["data"] or None,
            "sentimento":           resultado.get("sentimento", "neutro"),
            "alertas":              json.dumps(resultado.get("alertas", []), ensure_ascii=False),
            "clientes_mencionados": json.dumps(resultado.get("clientes_mencionados", []), ensure_ascii=False),
            "resumo_analise":       resultado.get("resumo_analise", ""),
            "analisado_em":         datetime.now(timezone.utc).isoformat(),
        }
        errors = _bq_client.insert_rows_json(table, [row], row_ids=[reuniao["fonte_id"]])
        if errors:
            logger.warning("[BQ] Erros ao inserir análise: %s", errors)
            return False
        return True
    except Exception as e:
        logger.warning("[BQ] Exceção ao inserir análise: %s", e)
        return False


# ── Claude ────────────────────────────────────────────────────────────────────

_claude = None

def get_claude():
    global _claude
    if _claude is None:
        _claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _claude


def analisar_com_claude(reuniao) -> dict:
    """Envia o texto da reunião ao Claude e retorna JSON estruturado."""
    texto = f"""Título: {reuniao['titulo']}
Data: {reuniao['data']} {reuniao['hora'] or ''}
Participantes: {reuniao['participantes'] or 'não informado'}

Resumo:
{reuniao['resumo'] or '(sem resumo)'}

Decisões:
{reuniao['decisoes'] or '(sem decisões registradas)'}

Próximas Etapas:
{reuniao['proximas_etapas'] or '(sem próximas etapas)'}

Detalhes:
{(reuniao['detalhes'] or '')[:4000]}"""

    client = get_claude()
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=PROMPT_SISTEMA,
        messages=[{"role": "user", "content": texto}],
    )
    raw = msg.content[0].text.strip()

    # Remove possível markdown
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)


# ── Slack ─────────────────────────────────────────────────────────────────────

EMOJI_SENTIMENTO = {
    "positivo": "✅",
    "neutro":   "📋",
    "negativo": "🔴",
}

EMOJI_ALERTA = {
    "sem_resposta": "🔇",
    "urgente":      "🚨",
    "sem_acesso":   "🔒",
    "conflito":     "⚠️",
    "bloqueio":     "🚫",
}

TIPOS_CRITICOS = {"sem_resposta", "urgente", "sem_acesso", "bloqueio"}


def enviar_slack(reuniao, resultado: dict, dry_run: bool = False):
    """Envia alerta no Slack se houver alertas críticos."""
    alertas = resultado.get("alertas", [])
    criticos = [a for a in alertas if a.get("tipo") in TIPOS_CRITICOS]

    if not criticos:
        return

    sentimento = resultado.get("sentimento", "neutro")
    emoji_s = EMOJI_SENTIMENTO.get(sentimento, "📋")

    blocos = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji_s} Alerta — Reunião: {reuniao['titulo']} ({reuniao['data']})",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Resumo:* {resultado.get('resumo_analise', '')}",
            },
        },
        {"type": "divider"},
    ]

    for alerta in criticos:
        tipo  = alerta.get("tipo", "")
        emoji = EMOJI_ALERTA.get(tipo, "⚠️")
        blocos.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Tipo:* {emoji} `{tipo}`"},
                {"type": "mrkdwn", "text": f"*Cliente:* {alerta.get('cliente', '?')}"},
                {"type": "mrkdwn", "text": f"*{alerta.get('descricao', '')}*"},
                {"type": "mrkdwn", "text": f"_{alerta.get('trecho', '')}_"},
            ],
        })

    clientes = resultado.get("clientes_mencionados", [])
    if clientes:
        blocos.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"👥 Clientes: {', '.join(clientes)}"}],
        })

    payload = {
        "channel": SLACK_ALERT_CHANNEL,
        "text": f"Alerta de reunião: {reuniao['titulo']}",
        "blocks": blocos,
    }

    if dry_run:
        logger.info("[DRY-RUN] Slack payload:\n%s", json.dumps(payload, indent=2, ensure_ascii=False))
        return

    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    data = resp.json()
    if data.get("ok"):
        logger.info("[Slack] Alerta enviado (%d alertas críticos)", len(criticos))
    else:
        logger.warning("[Slack] Falha: %s", data.get("error", "unknown"))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--todos",   action="store_true", help="Reanálisa todas as reuniões")
    parser.add_argument("--dry-run", action="store_true", help="Não salva nem envia Slack")
    args = parser.parse_args()

    logger.info("Iniciando análise de reuniões...")
    conn = get_conn()
    pendentes = reunioes_pendentes(conn, args.todos)
    logger.info("Reuniões para analisar: %d", len(pendentes))

    analisadas = 0
    alertas_total = 0
    erros = 0

    for reuniao in pendentes:
        titulo = reuniao["titulo"]
        logger.info("  🔍 Analisando: %s (%s)", titulo, reuniao["data"])

        try:
            resultado = analisar_com_claude(reuniao)

            n_alertas = len(resultado.get("alertas", []))
            sentimento = resultado.get("sentimento", "neutro")
            emoji_s = EMOJI_SENTIMENTO.get(sentimento, "📋")

            logger.info(
                "     %s sentimento=%s | alertas=%d | clientes=%s",
                emoji_s,
                sentimento,
                n_alertas,
                resultado.get("clientes_mencionados", []),
            )

            if not args.dry_run:
                salvar_analise_sqlite(conn, reuniao, resultado)
                bq_ok = salvar_analise_bq(reuniao, resultado)
                logger.info("     💾 SQLite ✅ | BQ %s", "✅" if bq_ok else "⚠️")

            enviar_slack(reuniao, resultado, dry_run=args.dry_run)

            analisadas += 1
            alertas_total += n_alertas

        except json.JSONDecodeError as e:
            logger.error("  ❌ JSON inválido do Claude em '%s': %s", titulo, e)
            erros += 1
        except Exception as e:
            logger.error("  ❌ Erro ao analisar '%s': %s", titulo, e)
            erros += 1

    conn.close()
    logger.info(
        "\nAnálise concluída — analisadas: %d | alertas: %d | erros: %d",
        analisadas, alertas_total, erros,
    )


if __name__ == "__main__":
    main()
