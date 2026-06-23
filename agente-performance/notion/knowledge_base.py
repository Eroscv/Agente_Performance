"""
notion/knowledge_base.py
────────────────────────
Memória de longo prazo do Agente de Performance Guará.

Fluxo:
  1. Toda resposta gerada é salva como PENDENTE (aprovado=False)
  2. Quando o usuário reage com 👍 no Slack, a entrada é APROVADA
  3. Antes de cada resposta do Claude, as top N entradas aprovadas para
     aquele intent/campanha são injetadas no system prompt como "memória"

Database Notion: 🧠 Base de Conhecimento — Source OF
  ID: 3888a727-a8ff-8163-9753-f7579b8a0bbe
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

logger = logging.getLogger(__name__)

# ── Configuração ──────────────────────────────────────────────────────────────

NOTION_KEY     = os.getenv("NOTION_API_KEY", "")
NOTION_VER     = "2022-06-28"
KB_DATABASE_ID = "3888a727-a8ff-8163-9753-f7579b8a0bbe"

_HEADERS = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Notion-Version": NOTION_VER,
    "Content-Type": "application/json",
}

# Número máximo de entradas de contexto injetadas no prompt
MAX_CONTEXT_ENTRIES = 5

# ── Helpers de propriedade ────────────────────────────────────────────────────

def _prop_title(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": text[:2000]}}]}

def _prop_rich_text(text: str) -> dict:
    # Notion rich_text tem limite de 2000 chars por elemento
    chunks = [text[i:i+2000] for i in range(0, min(len(text), 6000), 2000)]
    return {"rich_text": [{"type": "text", "text": {"content": c}} for c in chunks]}

def _prop_select(name: str) -> dict:
    return {"select": {"name": name}}

def _prop_number(n: int) -> dict:
    return {"number": n}

def _prop_checkbox(v: bool) -> dict:
    return {"checkbox": v}

def _prop_date(d: str) -> dict:
    return {"date": {"start": d}}

def _extract_rich_text(prop: dict) -> str:
    return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))

def _extract_title(prop: dict) -> str:
    return "".join(t.get("plain_text", "") for t in prop.get("title", []))

def _extract_select(prop: dict) -> Optional[str]:
    sel = prop.get("select")
    return sel["name"] if sel else None

# ── CRUD principal ────────────────────────────────────────────────────────────

def salvar_entrada(
    pergunta:   str,
    resposta:   str,
    intent:     str,
    campanha:   Optional[str] = None,
    data_ini:   Optional[str] = None,
    data_fim:   Optional[str] = None,
    slack_msg_ts: Optional[str] = None,
) -> Optional[str]:
    """
    Salva uma nova entrada (não aprovada) na base de conhecimento.
    Retorna o page_id criado, ou None em caso de erro.
    """
    # Título = resumo da pergunta (primeiros 100 chars)
    titulo = pergunta[:100]
    periodo = f"{data_ini} → {data_fim}" if data_ini and data_fim else (data_ini or "")

    properties = {
        "Pergunta":   _prop_title(titulo),
        "Resposta":   _prop_rich_text(resposta),
        "Intent":     _prop_select(intent),
        "Aprovado":   _prop_checkbox(False),
        "Frequencia": _prop_number(1),
        "Data":       _prop_date(date.today().isoformat()),
    }
    if campanha:
        properties["Campanha"] = _prop_rich_text(campanha)
    if periodo:
        properties["Periodo"] = _prop_rich_text(periodo)
    if slack_msg_ts:
        properties["Aprovado_por"] = _prop_rich_text(f"pending:{slack_msg_ts}")

    payload = {
        "parent": {"database_id": KB_DATABASE_ID},
        "properties": properties,
    }

    try:
        r = requests.post(
            "https://api.notion.com/v1/pages",
            headers=_HEADERS, json=payload, timeout=10
        )
        if r.status_code == 200:
            page_id = r.json()["id"]
            logger.debug("KB: entrada salva %s (intent=%s)", page_id, intent)
            return page_id
        else:
            logger.warning("KB: erro ao salvar entrada: %s", r.json().get("message"))
            return None
    except Exception as e:
        logger.warning("KB: exceção ao salvar: %s", e)
        return None


def aprovar_entrada(page_id: str, aprovado_por: str = "") -> bool:
    """
    Marca uma entrada como aprovada (👍 recebido no Slack).
    Também incrementa frequência e registra quem aprovou.
    """
    # Busca entrada atual para pegar frequência
    try:
        r = requests.get(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_HEADERS, timeout=5
        )
        if r.status_code != 200:
            logger.warning("KB: página não encontrada: %s", page_id)
            return False

        page = r.json()
        props = page.get("properties", {})
        freq_atual = props.get("Frequencia", {}).get("number") or 0

        # Atualiza: aprovado=True, frequência+1, aprovado_por
        patch = {
            "properties": {
                "Aprovado":   _prop_checkbox(True),
                "Frequencia": _prop_number(freq_atual + 1),
                "Aprovado_por": _prop_rich_text(aprovado_por[:200] if aprovado_por else "👍 Slack"),
            }
        }
        r2 = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_HEADERS, json=patch, timeout=5
        )
        ok = r2.status_code == 200
        if ok:
            logger.info("KB: entrada aprovada %s por %s", page_id, aprovado_por)
        return ok

    except Exception as e:
        logger.warning("KB: exceção ao aprovar: %s", e)
        return False


def incrementar_frequencia(page_id: str) -> bool:
    """Incrementa o contador de frequência quando a mesma pergunta é feita novamente."""
    try:
        r = requests.get(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_HEADERS, timeout=5
        )
        if r.status_code != 200:
            return False
        freq = r.json().get("properties", {}).get("Frequencia", {}).get("number") or 0
        r2 = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_HEADERS,
            json={"properties": {"Frequencia": _prop_number(freq + 1)}},
            timeout=5
        )
        return r2.status_code == 200
    except Exception:
        return False


def buscar_contexto(
    intent: Optional[str] = None,
    campanha: Optional[str] = None,
    limit: int = MAX_CONTEXT_ENTRIES,
) -> list[dict]:
    """
    Busca as entradas aprovadas mais frequentes para um intent/campanha.
    Retorna lista de dicts com pergunta, resposta, intent, campanha, frequencia.
    Usado para injetar contexto no prompt do Claude.
    """
    filtros = [{"property": "Aprovado", "checkbox": {"equals": True}}]

    if intent:
        filtros.append({"property": "Intent", "select": {"equals": intent}})

    payload: dict = {
        "filter": {"and": filtros} if len(filtros) > 1 else filtros[0],
        "sorts": [{"property": "Frequencia", "direction": "descending"}],
        "page_size": limit,
    }

    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{KB_DATABASE_ID}/query",
            headers=_HEADERS, json=payload, timeout=10
        )
        if r.status_code != 200:
            logger.warning("KB: erro na busca: %s", r.json().get("message"))
            return []

        results = []
        for page in r.json().get("results", []):
            props = page.get("properties", {})
            entry = {
                "page_id":   page["id"],
                "pergunta":  _extract_title(props.get("Pergunta", {})),
                "resposta":  _extract_rich_text(props.get("Resposta", {})),
                "intent":    _extract_select(props.get("Intent", {})),
                "campanha":  _extract_rich_text(props.get("Campanha", {})),
                "periodo":   _extract_rich_text(props.get("Periodo", {})),
                "frequencia": props.get("Frequencia", {}).get("number", 0),
            }
            # Filtra por campanha se especificada (fuzzy simples)
            if campanha and entry["campanha"]:
                camp_lower = campanha.lower()
                if camp_lower not in entry["campanha"].lower() and entry["campanha"].lower() not in camp_lower:
                    continue
            results.append(entry)

        return results[:limit]

    except Exception as e:
        logger.warning("KB: exceção na busca de contexto: %s", e)
        return []


def buscar_por_slack_ts(slack_ts: str) -> Optional[str]:
    """
    Busca o page_id da entrada associada a um timestamp de mensagem Slack.
    Usado para encontrar a entrada quando o usuário dá 👍 em uma resposta.
    """
    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{KB_DATABASE_ID}/query",
            headers=_HEADERS,
            json={
                "filter": {
                    "property": "Aprovado_por",
                    "rich_text": {"contains": f"pending:{slack_ts}"}
                },
                "page_size": 1,
            },
            timeout=10
        )
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
        return results[0]["id"] if results else None
    except Exception:
        return None


def formatar_contexto_para_prompt(entradas: list[dict]) -> str:
    """
    Formata as entradas aprovadas como bloco de texto para injetar no prompt do Claude.
    """
    if not entradas:
        return ""

    linhas = [
        "## 📚 Memória — respostas aprovadas anteriormente\n",
        "Use estas respostas como referência de qualidade e estilo. "
        "Adapte ao contexto atual sem copiar literalmente.\n",
    ]
    for i, e in enumerate(entradas, 1):
        freq = e.get("frequencia", 1)
        camp = f" · campanha: {e['campanha']}" if e.get("campanha") else ""
        per  = f" · período: {e['periodo']}" if e.get("periodo") else ""
        linhas.append(f"\n### Exemplo {i} (perguntado {freq}x{camp}{per})")
        linhas.append(f"**Pergunta:** {e['pergunta']}")
        # Trunca resposta longa para não explodir o prompt
        resp = e['resposta'][:800] + "..." if len(e['resposta']) > 800 else e['resposta']
        linhas.append(f"**Resposta:** {resp}")

    return "\n".join(linhas)


# ── Listar entradas (admin) ───────────────────────────────────────────────────

def listar_entradas(apenas_aprovadas: bool = False, limit: int = 20) -> list[dict]:
    """Lista entradas para administração / debug."""
    payload: dict = {"page_size": limit, "sorts": [
        {"property": "Frequencia", "direction": "descending"}
    ]}
    if apenas_aprovadas:
        payload["filter"] = {"property": "Aprovado", "checkbox": {"equals": True}}

    try:
        r = requests.post(
            f"https://api.notion.com/v1/databases/{KB_DATABASE_ID}/query",
            headers=_HEADERS, json=payload, timeout=10
        )
        if r.status_code != 200:
            return []

        results = []
        for page in r.json().get("results", []):
            props = page.get("properties", {})
            results.append({
                "page_id":   page["id"],
                "pergunta":  _extract_title(props.get("Pergunta", {})),
                "intent":    _extract_select(props.get("Intent", {})),
                "campanha":  _extract_rich_text(props.get("Campanha", {})),
                "aprovado":  props.get("Aprovado", {}).get("checkbox", False),
                "frequencia": props.get("Frequencia", {}).get("number", 0),
                "data":       (props.get("Data", {}).get("date") or {}).get("start"),
            })
        return results
    except Exception as e:
        logger.warning("KB listar_entradas erro: %s", e)
        return []
