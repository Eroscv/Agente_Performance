"""
notion/client.py

Cliente Notion para o Agente de Performance Guará.
Conecta ao workspace Guará Media e acessa os databases de relatórios.

Databases de relatórios por cliente:
  - Consórcio Sorteado  → ds=37a8a727-a8ff-8110-b005-000bf0126f94
  - Source OF           → ds=37a8a727-a8ff-81ba-900c-000b7df87c7c
  - Máximo Serviço      → ds=37a8a727-a8ff-8170-8e3b-000b70e5590f
  - BE MUSHIN           → ds=37a8a727-a8ff-81a3-88e2-000bb475bc39
  - Bazar do Precatório → ds=37a8a727-a8ff-81aa-800d-000b5bb61eb4

Outros databases relevantes:
  - Torre de Controle — Regras → ds=37a8a727-a8ff-81e7-a038-000bf73a3e79
  - Clientes                   → ds=30ecc6df-d8ec-4e56-8377-1cce7b0d6c83
"""

import logging
import os
import random
import time
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

NOTION_API_KEY  = os.getenv("NOTION_API_KEY", "")
NOTION_VERSION  = os.getenv("NOTION_VERSION", "2025-09-03")
BASE_URL        = "https://api.notion.com/v1"

# Retry config
_RETRY_MAX      = int(os.getenv("NOTION_RETRY_MAX", "5"))
_RETRY_DELAY    = float(os.getenv("NOTION_RETRY_DELAY", "2.0"))   # segundos iniciais
_RETRY_MAX_DELAY = float(os.getenv("NOTION_RETRY_MAX_DELAY", "60.0"))  # teto backoff
# Status HTTP que justificam retry (erros transitórios)
_RETRY_STATUSES = {429, 500, 502, 503, 504}

# ── IDs dos databases de relatórios ──────────────────────────────────────────
# data_source_id → para queries
# database_id    → para criar páginas (parent)

DATABASES = {
    "pod_kombucha": {
        "nome":           "Relatórios — POD Kombucha",
        "data_source_id": "37a8a727-a8ff-81ba-900c-000b7df87c7c",  # mesmo database da Source OF
        "database_id":    None,
    },
    "consorcio_sorteado": {
        "nome":           "Relatórios — Consórcio Sorteado",
        "data_source_id": "37a8a727-a8ff-8110-b005-000bf0126f94",
        "database_id":    "37a8a727-a8ff-814c-a1dc-e5e0b9f5993a",
    },
    "source_of": {
        "nome":           "Relatórios — Source OF",
        "data_source_id": "37a8a727-a8ff-81ba-900c-000b7df87c7c",
        "database_id":    None,  # obter na primeira chamada
    },
    "maximo_servico": {
        "nome":           "Relatórios — Máximo Serviço",
        "data_source_id": "37a8a727-a8ff-8170-8e3b-000b70e5590f",
        "database_id":    None,
    },
    "be_mushin": {
        "nome":           "Relatórios — BE MUSHIN",
        "data_source_id": "37a8a727-a8ff-81a3-88e2-000bb475bc39",
        "database_id":    None,
    },
    "bazar_precatorio": {
        "nome":           "Relatórios — Bazar do Precatório",
        "data_source_id": "37a8a727-a8ff-81aa-800d-000b5bb61eb4",
        "database_id":    None,
    },
    "torre_controle": {
        "nome":           "Torre de Controle — Regras",
        "data_source_id": "37a8a727-a8ff-81e7-a038-000bf73a3e79",
        "database_id":    None,
    },
    "clientes": {
        "nome":           "Clientes",
        "data_source_id": "30ecc6df-d8ec-4e56-8377-1cce7b0d6c83",
        "database_id":    None,
    },
}


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request_with_retry(method: str, path: str, **kwargs) -> dict:
    """
    Executa uma requisição HTTP à API do Notion com retry + backoff exponencial.

    Faz até _RETRY_MAX tentativas nos status transitórios (429/5xx) e em
    erros de conexão/timeout. Levanta a última exceção se esgotar as tentativas.
    """
    url   = f"{BASE_URL}{path}"
    delay = _RETRY_DELAY

    for tentativa in range(1, _RETRY_MAX + 1):
        try:
            resp = requests.request(method, url, headers=_headers(), timeout=20, **kwargs)

            # Resposta bem-sucedida
            if resp.status_code < 400:
                if tentativa > 1:
                    logger.info("[Notion] %s %s OK na tentativa %d", method, path, tentativa)
                return resp.json()

            # Erros que vale retentir
            if resp.status_code in _RETRY_STATUSES and tentativa < _RETRY_MAX:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else delay
                jitter = random.uniform(0, wait * 0.3)
                wait_total = wait + jitter
                logger.warning(
                    "[Notion] %s %s → HTTP %d (tentativa %d/%d). Aguardando %.1fs (jitter +%.1fs)...",
                    method, path, resp.status_code, tentativa, _RETRY_MAX, wait, jitter,
                )
                time.sleep(wait_total)
                delay = min(delay * 2, _RETRY_MAX_DELAY)
                continue

            # Erro não retriável (4xx exceto 429) — levanta imediatamente
            resp.raise_for_status()

        except (requests.ConnectionError, requests.Timeout) as exc:
            if tentativa < _RETRY_MAX:
                jitter = random.uniform(0, delay * 0.3)
                wait_total = delay + jitter
                logger.warning(
                    "[Notion] %s %s — %s (tentativa %d/%d). Aguardando %.1fs (jitter +%.1fs)...",
                    method, path, exc, tentativa, _RETRY_MAX, delay, jitter,
                )
                time.sleep(wait_total)
                delay = min(delay * 2, _RETRY_MAX_DELAY)
            else:
                logger.error("[Notion] %s %s — desistindo após %d tentativas: %s", method, path, _RETRY_MAX, exc)
                raise

    # Não deve chegar aqui, mas garante o raise para mypy
    raise RuntimeError(f"[Notion] {method} {path} falhou após {_RETRY_MAX} tentativas")


def _get(path: str) -> dict:
    return _request_with_retry("GET", path)


def _post(path: str, body: dict) -> dict:
    return _request_with_retry("POST", path, json=body)


def _patch(path: str, body: dict) -> dict:
    return _request_with_retry("PATCH", path, json=body)


# ── Operações básicas ─────────────────────────────────────────────────────────

def testar_conexao() -> dict:
    """Verifica se a chave API está válida e retorna info do bot."""
    return _get("/users/me")


def buscar(query: str = "", tipo: str = None, limit: int = 20) -> list[dict]:
    """Busca páginas e databases no workspace."""
    body: dict[str, Any] = {"page_size": limit}
    if query:
        body["query"] = query
    if tipo in ("page", "database", "data_source"):
        body["filter"] = {"value": tipo, "property": "object"}
    result = _post("/search", body)
    return result.get("results", [])


def get_pagina(page_id: str) -> dict:
    """Retorna metadados de uma página."""
    return _get(f"/pages/{page_id}")


def get_blocos(page_id: str) -> list[dict]:
    """Retorna blocos (conteúdo) de uma página."""
    result = _get(f"/blocks/{page_id}/children")
    return result.get("results", [])


def get_database(database_id: str) -> dict:
    """Retorna metadados de um database."""
    return _get(f"/databases/{database_id}")


# ── Queries em data_sources ───────────────────────────────────────────────────

def query_database(
    data_source_id: str,
    filtros: dict = None,
    sorts: list = None,
    limit: int = 50,
) -> list[dict]:
    """
    Consulta um data_source (database) com filtros e ordenação opcionais.
    
    Args:
        data_source_id: ID do data_source (não database_id)
        filtros: dict de filtro Notion (ex: {"property": "Data", "date": {"past_week": {}}})
        sorts: lista de sorts (ex: [{"property": "Data", "direction": "descending"}])
        limit: máx de resultados
    
    Returns:
        Lista de páginas (cada uma tem 'properties')
    """
    body: dict[str, Any] = {"page_size": min(limit, 100)}
    if filtros:
        body["filter"] = filtros
    if sorts:
        body["sorts"] = sorts
    result = _post(f"/data_sources/{data_source_id}/query", body)
    return result.get("results", [])


def criar_pagina(database_id: str, properties: dict) -> dict:
    """
    Cria uma nova página (entrada) em um database.
    
    Args:
        database_id: ID do database (não data_source_id)
        properties: dict de propriedades no formato Notion
    
    Returns:
        Página criada
    """
    body = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    return _post("/pages", body)


def atualizar_pagina(page_id: str, properties: dict) -> dict:
    """Atualiza propriedades de uma página existente."""
    return _patch(f"/pages/{page_id}", {"properties": properties})


def adicionar_blocos(page_id: str, blocos: list[dict]) -> dict:
    """Adiciona conteúdo (blocos) ao final de uma página."""
    return _patch(f"/blocks/{page_id}/children", {"children": blocos})


# ── Helpers de extração de propriedades ──────────────────────────────────────

def extrair_texto(prop: dict) -> str:
    """Extrai texto de qualquer tipo de propriedade Notion."""
    ptype = prop.get("type", "")
    if ptype == "title":
        return "".join(p.get("plain_text", "") for p in prop.get("title", []))
    elif ptype == "rich_text":
        return "".join(p.get("plain_text", "") for p in prop.get("rich_text", []))
    elif ptype == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    elif ptype == "multi_select":
        return ", ".join(o.get("name", "") for o in prop.get("multi_select", []))
    elif ptype == "number":
        v = prop.get("number")
        return str(v) if v is not None else ""
    elif ptype == "date":
        d = prop.get("date")
        if d:
            return d.get("start", "") + (" → " + d["end"] if d.get("end") else "")
        return ""
    elif ptype == "checkbox":
        return "✅" if prop.get("checkbox") else "☐"
    elif ptype == "url":
        return prop.get("url", "") or ""
    elif ptype == "email":
        return prop.get("email", "") or ""
    elif ptype == "phone_number":
        return prop.get("phone_number", "") or ""
    return ""


def pagina_para_dict(page: dict) -> dict:
    """Converte uma página Notion para dict Python com todos os campos extraídos."""
    props = page.get("properties", {})
    return {k: extrair_texto(v) for k, v in props.items()}


# ── Helpers de criação de propriedades ───────────────────────────────────────

def prop_titulo(texto: str) -> dict:
    return {"title": [{"text": {"content": texto}}]}


def prop_texto(texto: str) -> dict:
    """Propriedade rich_text com chunking automático (limite Notion: 2000 chars/elemento)."""
    chunks = [texto[i:i+2000] for i in range(0, max(len(texto), 1), 2000)]
    return {"rich_text": [{"text": {"content": c}} for c in chunks]}


def prop_numero(valor: float) -> dict:
    return {"number": valor}


def prop_select(opcao: str) -> dict:
    return {"select": {"name": opcao}}


def prop_data(inicio: str, fim: str = None) -> dict:
    d: dict[str, Any] = {"date": {"start": inicio}}
    if fim:
        d["date"]["end"] = fim
    return d


def prop_checkbox(valor: bool) -> dict:
    return {"checkbox": valor}


# ── Helpers para os databases de relatórios ──────────────────────────────────
# Implementação movida para notion/relatorio.py (lógica de negócio separada do HTTP).
# Wrappers com lazy import para evitar importação circular.

def get_relatorios_cliente(cliente_key, limit=30, data_inicio=None, data_fim=None):
    """⚠️  Alias — implementação em notion/relatorio.py."""
    from notion.relatorio import get_relatorios_cliente as _fn
    return _fn(cliente_key=cliente_key, limit=limit, data_inicio=data_inicio, data_fim=data_fim)


def publicar_relatorio(cliente_key, titulo, plataforma, data, investimento, roas,
                       conversoes, cliente_nome=None, conteudo_blocos=None):
    """⚠️  Alias — implementação em notion/relatorio.py."""
    from notion.relatorio import publicar_relatorio as _fn
    return _fn(
        cliente_key=cliente_key, titulo=titulo, plataforma=plataforma,
        data=data, investimento=investimento, roas=roas, conversoes=conversoes,
        cliente_nome=cliente_nome, conteudo_blocos=conteudo_blocos,
    )


def listar_databases_disponiveis() -> list[dict]:
    """Retorna a lista de todos os databases configurados."""
    return [
        {"key": k, "nome": v["nome"], "data_source_id": v["data_source_id"]}
        for k, v in DATABASES.items()
    ]
