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

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
AGENTE_DIR = BASE_DIR.parent / "agente-performance"
ENV_FILE   = AGENTE_DIR / ".env"
DB_PATH    = BASE_DIR / "db" / "reunioes.db"

load_dotenv(ENV_FILE)

NOTION_KEY = os.getenv("NOTION_KEY", "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")
NOTION_DB_ID = "80b08108-7f14-4d79-b6dc-69e38c8de57d"
NOTION_VERSION = "2025-09-03"

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


# ── Parser de próximas etapas ─────────────────────────────────────────────────

def parsear_tarefas(texto: str, titulo_reuniao: str) -> list[dict]:
    """
    Extrai tarefas do texto de próximas etapas.
    Formato esperado: [Nome] Título: Descrição
    """
    if not texto or "não foram encontradas" in texto.lower():
        return []

    tarefas = []
    # Padrão principal: [Nome] Título: Descrição
    padrao = re.compile(
        r'\[([^\]]+)\]\s*([^:\n]+):\s*([^\[]+)',
        re.DOTALL
    )
    matches = padrao.findall(texto)

    for nome_raw, titulo_raw, desc_raw in matches:
        nome  = nome_raw.strip()
        titulo = titulo_raw.strip()
        desc   = desc_raw.strip().replace('\n', ' ')

        uid = None
        for key, uid_val in USUARIOS.items():
            if key.lower() in nome.lower() or nome.lower() in key.lower():
                uid = uid_val
                nome = key
                break

        if not uid:
            logger.warning("Usuário não mapeado: '%s' — pulando", nome)
            continue

        tarefas.append({
            "responsavel_nome": nome,
            "responsavel_id":   uid,
            "titulo":           titulo,
            "descricao":        desc[:500],
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


def tarefas_ja_criadas(fonte_id: str) -> set:
    """Busca tarefas já criadas com esse fonte_id para evitar duplicatas."""
    resp = requests.post(
        f"https://api.notion.com/v1/data_sources/7031dca2-547f-461a-a6a5-8be4ebf44569/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {
                "property": "Origem Reunião",
                "rich_text": {"contains": fonte_id[:8]}
            }
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return set()
    results = resp.json().get("results", [])
    titulos = set()
    for r in results:
        props = r.get("properties", {})
        t = props.get("Tarefa", {})
        titulo = "".join([x.get("plain_text", "") for x in t.get("title", [])])
        titulos.add(titulo)
    return titulos


def criar_card_notion(
    tarefa: dict,
    reuniao_data: str,
    reuniao_titulo: str,
    fonte_id: str,
    clientes: list[str],
    area: str,
    dry_run: bool = False,
) -> bool:
    """Cria um card no Notion. Retorna True se criado."""

    # Data de entrega = data da reunião + 7 dias (prazo padrão)
    try:
        dt = datetime.strptime(reuniao_data, "%Y-%m-%d").date()
        prazo = (dt + timedelta(days=7)).isoformat()
    except Exception:
        prazo = date.today().isoformat()

    revisor_id = REVISOR_PADRAO.get(area, "dab34a81-2f1b-4708-b930-b8f54da78e7f")

    properties = {
        "Tarefa": {
            "title": [{"text": {"content": tarefa["titulo"]}}]
        },
        "Responsável atual": {
            "people": [{"id": tarefa["responsavel_id"]}]
        },
        "Quem - todos envolvidos": {
            "people": [{"id": tarefa["responsavel_id"]}]
        },
        "Revisor": {
            "people": [{"id": revisor_id}]
        },
        "Data": {
            "date": {"start": prazo}
        },
        "Status": {
            "multi_select": [{"name": "A fazer"}]
        },
        "Tipo": {
            "select": {"name": "Reunião"}
        },
        "Área": {
            "select": {"name": area}
        },
    }

    if clientes:
        properties["Cliente"] = {
            "multi_select": [{"name": c} for c in clientes]
        }

    # Grava fonte_id aqui — é o que tarefas_ja_criadas() consulta (filtro
    # "contains" em fonte_id[:8]) para não duplicar card em reprocessamento.
    # Antes desta correção, essa propriedade nunca era escrita, então a
    # checagem de dedup (quando chamada) sempre voltava vazia mesmo que
    # cards já existissem.
    properties["Origem Reunião"] = {
        "rich_text": [{"text": {"content": fonte_id}}]
    }

    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": properties,
    }

    # Adiciona descrição como bloco de texto
    children = []
    if tarefa.get("descricao"):
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"text": {"content": tarefa["descricao"]}}]
            }
        })
    children.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"text": {"content": f"📅 Origem: {reuniao_titulo} ({reuniao_data})"}}]
        }
    })

    payload["children"] = children

    if dry_run:
        logger.info(
            "  [DRY-RUN] %s → %s | área=%s | clientes=%s | prazo=%s",
            tarefa["responsavel_nome"], tarefa["titulo"], area, clientes, prazo
        )
        return True

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=payload,
        timeout=15,
    )
    if resp.status_code == 200:
        return True
    else:
        logger.warning("Erro ao criar card: %s — %s", resp.status_code, resp.text[:200])
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--todos",   action="store_true", help="Processa todas as reuniões")
    parser.add_argument("--dry-run", action="store_true", help="Não cria no Notion")
    args = parser.parse_args()

    # Orquestração delegada ao ComunicacaoAgent (Camada 4) — ver
    # agents/comunicacao.py. Import local: evita import circular
    # (agents.comunicacao importa este módulo).
    from agents.comunicacao import ComunicacaoAgent
    ComunicacaoAgent().executar(todos=args.todos, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
