"""
criar_tarefas_notion.py

Lê as próximas etapas das reuniões mais recentes do SQLite,
parseia as tarefas por responsável e cria cards no Notion
(database Agenda e Registros).

Uso:
  python3 criar_tarefas_notion.py              # reuniões dos últimos 30 dias
  python3 criar_tarefas_notion.py --todos      # todas as reuniões
  python3 criar_tarefas_notion.py --dry-run    # sem criar no Notion
"""

import argparse
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from tz import hoje_brt, agora_utc_iso
from card_validacao import validar_card, sugerir_area

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
AGENTE_DIR = BASE_DIR.parent / "agente-performance"
ENV_FILE   = AGENTE_DIR / ".env"
DB_PATH    = BASE_DIR / "db" / "reunioes.db"

load_dotenv(ENV_FILE)

NOTION_KEY = os.getenv("NOTION_API_KEY", os.getenv("NOTION_KEY", ""))
NOTION_DB_ID = "80b08108-7f14-4d79-b6dc-69e38c8de57d"
NOTION_VERSION = "2022-06-28"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Mapeamento de usuários Notion ─────────────────────────────────────────────

USUARIOS = {
    "Aline Satur":        "3d5d872b-594c-811b-bebe-00025d163708",
    "Aline":              "3d5d872b-594c-811b-bebe-00025d163708",
    "Bianca Garbin":      "184d872b-594c-814f-8946-0002dcaefdcf",
    "Bianca":             "184d872b-594c-814f-8946-0002dcaefdcf",
    "Eros Vital":         "261d872b-594c-81c9-b194-000213f99aed",
    "Eros":               "261d872b-594c-81c9-b194-000213f99aed",
    "Guilherme Oliveira": "3bcd872b-594c-81ec-9fa2-0002afed6b7a",
    "Guilherme":          "3bcd872b-594c-81ec-9fa2-0002afed6b7a",
    "Isabela Rodrigues":  "183d872b-594c-81c0-86c8-0002c0bab1be",
    "Isabela":            "183d872b-594c-81c0-86c8-0002c0bab1be",
    "Isa":                "183d872b-594c-81c0-86c8-0002c0bab1be",
    "Matheus Ferreira":   "1f2d872b-594c-81ef-8323-0002da953a20",
    "Matheus":            "1f2d872b-594c-81ef-8323-0002da953a20",
    "Miguel Ticianeli":   "dab34a81-2f1b-4708-b930-b8f54da78e7f",
    "Miguel":             "dab34a81-2f1b-4708-b930-b8f54da78e7f",
}

# Revisor padrão por área
REVISOR_PADRAO = {
    "Performance": "dab34a81-2f1b-4708-b930-b8f54da78e7f",  # Miguel
    "3P":          "dab34a81-2f1b-4708-b930-b8f54da78e7f",  # Miguel
    "Gestão":      "dab34a81-2f1b-4708-b930-b8f54da78e7f",  # Miguel
}

# Mapeamento de cliente por palavras-chave no título/texto
CLIENTES_KEYWORDS = {
    "source of":   "Source Of",
    "source_of":   "Source Of",
    "sorosof":     "Source Of",
    "sof":         "Source Of",
    "pod":         "POD",
    "kombucha":    "POD",
    "liv up":      "Liv Up",
    "livup":       "Liv Up",
    "botica":      "Botica",
    "feq":         "Botica",
    "mushin":      "Source Of",
}

# Área por tipo de reunião
AREA_KEYWORDS = {
    "performance": "Performance",
    "3p":          "3P",
    "media":       "Performance",
    "mídia":       "Performance",
    "diaria":      "Performance",
    "diária":      "Performance",
    "semanal":     "Performance",
    "briefing":    "3P",
    "publicidade": "3P",
    "arte":        "3P",
    "gestão":      "Gestão",
    "gestao":      "Gestão",
    "estudos":     "Gestão",
}


# ── Detecção de prazos na transcrição ───────────────────────────────────────

# Mapeamento de nomes de meses BR
_MESES_BR = {
    "janeiro": 1,  "fevereiro": 2,  "março": 3,    "marco": 3,
    "abril": 4,    "maio": 5,       "junho": 6,
    "julho": 7,    "agosto": 8,     "setembro": 9,
    "outubro": 10, "novembro": 11,  "dezembro": 12,
}

# Padrões de prazo mais comuns em transcrições BR
_PRAZO_RES = [
    # "até 05/11" | "até 05/11/2026"
    re.compile(r'(?:at[eé]|entrega\s+em|prazo[:\s]+)\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?', re.IGNORECASE),
    # "até dia 5 de novembro" | "até 5 de novembro"
    re.compile(r'at[eé]\s+(?:dia\s+)?(\d{1,2})\s+de\s+(' + '|'.join(_MESES_BR) + r')(?:\s+de\s+(\d{4}))?', re.IGNORECASE),
    # "prazo: sexta" | "até sexta" | "até segunda-feira"
    re.compile(r'(?:at[eé]|prazo[:\s]+)\s*(segunda(?:-feira)?|terça(?:-feira)?|quarta(?:-feira)?|quinta(?:-feira)?|sexta(?:-feira)?)', re.IGNORECASE),
    # "essa semana" | "essa sprint"
    re.compile(r'(?:essa|esta|próxima)\s+(semana|sprint)', re.IGNORECASE),
]

# Dias da semana para calcular data futura
_DIAS_SEMANA = {
    'segunda': 0, 'terça': 1, 'quarta': 2, 'quinta': 3, 'sexta': 4,
}


def _proximo_dia_semana(nome_dia: str) -> date:
    """Retorna a data do próximo dia da semana especificado."""
    hoje     = hoje_brt()
    hoje_dow = hoje.weekday()  # 0=seg ... 6=dom
    nome_lower = nome_dia.lower().split('-')[0].strip()  # "sexta-feira" → "sexta"
    alvo_dow = _DIAS_SEMANA.get(nome_lower, 4)           # default sexta
    delta    = (alvo_dow - hoje_dow) % 7
    delta    = delta if delta > 0 else 7  # sempre futuro
    return hoje + timedelta(days=delta)


def extrair_prazo(texto: str, data_reuniao: str | None = None) -> str:
    """
    Tenta extrair uma data de prazo explícita do texto fornecido.
    Retorna string ISO (YYYY-MM-DD) ou None se não encontrar.

    Ordem de prioridade:
      1. Data numérica (05/11 ou 05/11/2026)
      2. Data por extenso (5 de novembro)
      3. Dia da semana (sexta, segunda-feira)
      4. "essa semana" / "próxima semana"
    """
    hoje = hoje_brt()
    ano_ref = hoje.year
    texto_lower = texto.lower()

    # 1. Data numérica
    m = _PRAZO_RES[0].search(texto)
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        ano_str  = m.group(3)
        if ano_str:
            ano = int(ano_str) if len(ano_str) == 4 else 2000 + int(ano_str)
        else:
            ano = ano_ref
        try:
            dt = date(ano, mes, dia)
            if dt < hoje:  # data já passou → assume próximo ano
                dt = date(ano + 1, mes, dia)
            return dt.isoformat()
        except ValueError:
            pass

    # 2. Data por extenso ("5 de novembro")
    m = _PRAZO_RES[1].search(texto)
    if m:
        dia     = int(m.group(1))
        mes     = _MESES_BR.get(m.group(2).lower())
        ano_str = m.group(3)
        ano     = int(ano_str) if ano_str else ano_ref
        if mes:
            try:
                dt = date(ano, mes, dia)
                if dt < hoje:
                    dt = date(ano + 1, mes, dia)
                return dt.isoformat()
            except ValueError:
                pass

    # 3. Dia da semana
    m = _PRAZO_RES[2].search(texto)
    if m:
        return _proximo_dia_semana(m.group(1)).isoformat()

    # 4. "essa semana" → próxima sexta
    m = _PRAZO_RES[3].search(texto)
    if m:
        token = m.group(1).lower()
        if 'semana' in token:
            return _proximo_dia_semana('sexta').isoformat()
        # sprint → +14 dias
        return (hoje + timedelta(days=14)).isoformat()

    return None


# ── Parser de próximas etapas ─────────────────────────────────────────────────

def parsear_tarefas(texto: str, titulo_reuniao: str) -> list[dict]:
    """
    Extrai tarefas do texto de próximas etapas.
    Suporta dois formatos gerados pelo Claude:

    Formato A (com colchetes):  [Nome] Título: Descrição
    Formato B (com parênteses): Título (Nome)\nDescrição\n
    """
    if not texto or "não foram encontradas" in texto.lower():
        return []

    # ── Formato A: [Nome] Título: Descrição ──────────────────────────────────
    padrao_a = re.compile(r'\[([^\]]+)\]\s*([^:\n]+):\s*([^\[]+)', re.DOTALL)
    matches_a = padrao_a.findall(texto)

    # ── Formato A2: [Nome] Título (sem dois-pontos e descrição) ─────────────────
    # Cada linha: [Nome] Texto até a próxima linha
    padrao_a2 = re.compile(r'\[([^\]]+)\]\s*([^\n\[]+)', re.MULTILINE)
    matches_a2 = padrao_a2.findall(texto)

    # ── Formato B: Título (Nome)\nDescrição ──────────────────────────────────
    padrao_b = re.compile(
        r'^(.+?)\s+\(([^)]+)\)\s*\n((?:(?!^.+\([^)]+\)\s*$).+\n?)+)',
        re.MULTILINE,
    )
    matches_b = padrao_b.findall(texto)

    # Prioridade: A (com descrição) > A2 (só título) > B (nome no parêntese)
    if matches_a:
        raw_matches = [(m[0], m[1], m[2]) for m in matches_a]
    elif matches_b and len(matches_b) >= len(matches_a2):
        raw_matches = [(m[1], m[0], m[2]) for m in matches_b]  # (nome, titulo, desc)
    elif matches_a2:
        # Formato sem descrição: titulo = texto completo, desc = vazio
        raw_matches = [(m[0], m[1].strip(), "") for m in matches_a2]
    else:
        raw_matches = []

    tarefas = []
    for nome_raw, titulo_raw, desc_raw in raw_matches:
        nome   = nome_raw.strip()
        titulo = titulo_raw.strip()
        desc   = desc_raw.strip().replace('\n', ' ')

        uid = None
        for key, uid_val in USUARIOS.items():
            if key.lower() in nome.lower() or nome.lower() in key.lower():
                uid = uid_val
                nome = key
                break

        if not uid:
            logger.warning("Usuário não mapeado: '%s' - pulando", nome)
            continue

        prazo_detectado = extrair_prazo(titulo + ' ' + desc)

        tarefas.append({
            "responsavel_nome": nome,
            "responsavel_id":   uid,
            "titulo":           titulo,
            "descricao":        desc[:500],
            "prazo_detectado":  prazo_detectado,
        })

    return tarefas


def detectar_clientes(texto: str) -> list[str]:
    texto_lower = texto.lower()
    clientes = []
    vistos = set()
    for kw, cliente in CLIENTES_KEYWORDS.items():
        if kw in texto_lower and cliente not in vistos:
            clientes.append(cliente)
            vistos.add(cliente)
    return clientes


def detectar_area(titulo: str) -> str:
    titulo_lower = titulo.lower()
    for kw, area in AREA_KEYWORDS.items():
        if kw in titulo_lower:
            return area
    return "Performance"


# ── Notion ────────────────────────────────────────────────────────────────────

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


# ── Guardrails de dedup ──────────────────────────────────────────────────────

def _get_db_conn() -> sqlite3.Connection:
    """Conexão leve ao SQLite local (somente leitura p/ dedup)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cards_ja_criados_local(fonte_id: str) -> set:
    """
    Guardrail primário: consulta SQLite local.
    Retorna conjunto de títulos de tarefas já criadas para essa reunião.
    Rápido, offline, sem dependência de API.
    """
    try:
        conn = _get_db_conn()
        rows = conn.execute(
            "SELECT titulo_tarefa FROM cards_notion WHERE fonte_id = ?",
            (fonte_id,)
        ).fetchall()
        conn.close()
        return {r["titulo_tarefa"] for r in rows}
    except Exception as e:
        logger.warning("[dedup-local] Falha ao consultar SQLite: %s", e)
        return set()


def registrar_card_local(fonte_id: str, titulo_tarefa: str, url_notion: str = "") -> bool:
    """
    Registra card criado no SQLite local.
    Usa INSERT OR IGNORE para garantir idempotência.
    Retorna True se inseriu (novo), False se já existia.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        agora = agora_utc_iso()
        cur = conn.execute(
            "INSERT OR IGNORE INTO cards_notion (fonte_id, titulo_tarefa, url_notion, criado_em) VALUES (?,?,?,?)",
            (fonte_id, titulo_tarefa, url_notion, agora)
        )
        conn.commit()
        inserido = cur.rowcount > 0
        conn.close()
        return inserido
    except Exception as e:
        logger.warning("[dedup-local] Falha ao registrar card: %s", e)
        return False


def cards_ja_criados_notion(fonte_id: str) -> set:
    """
    Guardrail secundário: títulos de todas as tarefas Tipo=Reunião no Notion (paginado).
    Falha fechada: erro na API levanta exceção e o chamador pula a reunião, em vez de
    seguir "sem dedup" e duplicar cards. Antes lia só as 100 primeiras e devolvia vazio no erro.
    """
    titulos, cursor = set(), None
    while True:
        corpo = {"filter": {"property": "Tipo", "select": {"equals": "Reunião"}}, "page_size": 100}
        if cursor:
            corpo["start_cursor"] = cursor
        resp = requests.post(f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
                             headers=NOTION_HEADERS, json=corpo, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"[dedup-notion] Erro {resp.status_code}: {resp.text[:100]}")
        j = resp.json()
        for r in j.get("results", []):
            t = r.get("properties", {}).get("Tarefa", {})
            titulo = "".join(x.get("plain_text", "") for x in t.get("title", []))
            if titulo:
                titulos.add(titulo)
        if not j.get("has_more"):
            return titulos
        cursor = j.get("next_cursor")


def tarefas_ja_criadas(fonte_id: str) -> set:
    """
    Interface pública de dedup - compatível com ComunicacaoAgent.
    Prioridade: SQLite local → Notion API (fallback).
    Se SQLite tem dados, confia nele e não consulta Notion.
    Se SQLite vazio, consulta Notion e sincroniza o resultado localmente.
    """
    local = cards_ja_criados_local(fonte_id)
    if local:
        return local

    # Fallback: sem dados locais → consulta Notion e popula o cache local
    notion = cards_ja_criados_notion(fonte_id)
    if notion:
        logger.info("[dedup] Sincronizando %d cards do Notion para SQLite (fonte_id=%s)",
                    len(notion), fonte_id[:12])
        for titulo in notion:
            registrar_card_local(fonte_id, titulo)
    return notion


def criar_card_notion(
    tarefa: dict,
    reuniao_data: str,
    reuniao_titulo: str,
    fonte_id: str,
    clientes: list[str],
    area: str,
    dry_run: bool = False,
) -> bool:
    """Cria um card no database Agenda e Registros do Notion."""

    prazo_detectado = tarefa.get("prazo_detectado")
    try:
        if prazo_detectado:
            prazo = prazo_detectado
        else:
            dt    = datetime.strptime(reuniao_data, "%Y-%m-%d").date()
            prazo = (dt + timedelta(days=7)).isoformat()
    except Exception:
        prazo = hoje_brt().isoformat()

    revisor_id = REVISOR_PADRAO.get(area, "dab34a81-2f1b-4708-b930-b8f54da78e7f")

    # Schema real: Tarefa(title), Responsável atual(people), Revisor(people),
    # Status(select), Tipo(select), Área(select), Cliente(multi_select), Data(date)
    properties = {
        "Tarefa": {
            "title": [{"text": {"content": tarefa["titulo"]}}]
        },
        "Responsável atual": {
            "people": [{"id": tarefa["responsavel_id"]}]
        },
        "Revisor": {
            "people": [{"id": revisor_id}]
        },
        "Status": {
            "select": {"name": "A fazer"}
        },
        "Tipo": {
            "select": {"name": "Reunião"}
        },
        "Área": {
            "select": {"name": area}
        },
        "Data": {
            "date": {"start": prazo}
        },
    }

    CLIENTES_VALIDOS = {"Source Of", "POD", "Botica", "Todos", "Bazar", "Liv Up", "Noni", "Extecamp"}
    clientes_filtrados = [c for c in clientes if c in CLIENTES_VALIDOS]
    if clientes_filtrados:
        properties["Cliente"] = {
            "multi_select": [{"name": c} for c in clientes_filtrados]
        }

    # Corpo: descrição + rastreabilidade (fonte_id para dedup)
    body_parts = []
    if tarefa.get("descricao"):
        body_parts.append(tarefa["descricao"])
    body_parts.append(f"\n📅 Origem: {reuniao_titulo} ({reuniao_data})")
    body_parts.append(f"👤 Responsável: {tarefa['responsavel_nome']}")
    body_parts.append(f"🔗 fonte_id: {fonte_id}")
    body_text = "\n".join(body_parts)

    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": properties,
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": body_text[:2000]}}]
                }
            }
        ]
    }

    # ── Validação antes de criar ──────────────────────────────────────────────
    ok_valido, erros_valid = validar_card(tarefa, area, reuniao_titulo)
    if not ok_valido:
        logger.error(
            "  [VALIDACAO-FALHA] Pulando card inválido: %s | erros: %s",
            tarefa.get("titulo", ""), erros_valid,
        )
        return False  # card inválido — não cria no Notion

    # Sugere correção de área se inconsistente
    area_sugerida = sugerir_area(tarefa.get("titulo", ""))
    if area_sugerida and area_sugerida != area:
        logger.warning(
            "  [VALIDACAO-AREA] Título sugere área '%s' mas foi passado '%s' para '%s'",
            area_sugerida, area, tarefa.get("titulo", "")[:60],
        )

    if dry_run:
        logger.info(
            "  [DRY-RUN] %s → %s | área=%s | clientes=%s | prazo=%s",
            tarefa["responsavel_nome"], tarefa["titulo"], area, clientes_filtrados, prazo
        )
        return True

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=payload,
        timeout=15,
    )
    if resp.status_code == 200:
        data     = resp.json()
        page_id  = data.get("id", "").replace("-", "")
        page_url = data.get("url", f"https://app.notion.com/p/{page_id}")
        logger.debug("[notion] Card criado: %s", page_url)
        return page_url
    else:
        logger.warning("Erro ao criar card: %s — %s", resp.status_code, resp.text[:150])
        return False

def notificar_cards_slack(
    cards: list[dict],
    reuniao_titulo: str,
    reuniao_data: str,
    dry_run: bool = False,
) -> bool:
    """
    Envia mensagem no Slack com a lista de cards criados no Notion.

    cards: lista de dicts com chaves:
        titulo, responsavel_nome, prazo, url_notion

    Retorna True se enviou com sucesso.
    """
    if not cards:
        return False

    if dry_run:
        logger.info("[DRY-RUN] Notificação Slack: %d cards da reunião '%s'", len(cards), reuniao_titulo)
        return True

    if not SLACK_BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN não configurado - notificação Slack ignorada")
        return False

    linhas = [
        f":white_check_mark: *{len(cards)} card(s) criado(s) no Notion*",
        f":spiral_note_pad: Reunião: *{reuniao_titulo}* ({reuniao_data})",
        "",
    ]
    for card in cards:
        prazo_txt = f" · prazo {card['prazo']}" if card.get("prazo") else ""
        url       = card.get("url_notion", "")
        link_txt  = f" - <{url}|abrir no Notion>" if url else ""
        linhas.append(
            f">  :bust_in_silhouette: *{card['responsavel_nome']}* - {card['titulo']}{prazo_txt}{link_txt}"
        )

    texto = "\n".join(linhas)

    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"channel": SLACK_ALERT_CHANNEL, "text": texto, "mrkdwn": True},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            logger.info("Slack: notificado (%d cards)", len(cards))
            return True
        else:
            logger.warning("Slack erro: %s", data.get("error", resp.text[:100]))
            return False
    except Exception as e:
        logger.warning("Slack exception: %s", e)
        return False

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--todos",   action="store_true", help="Processa todas as reuniões")
    parser.add_argument("--dry-run", action="store_true", help="Não cria no Notion")
    args = parser.parse_args()

    # Orquestração delegada ao ComunicacaoAgent (Camada 4) - ver
    # agents/comunicacao.py. Import local: evita import circular
    # (agents.comunicacao importa este módulo).
    from agents.comunicacao import ComunicacaoAgent
    ComunicacaoAgent().executar(todos=args.todos, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
