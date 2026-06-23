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
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

NOTION_API_KEY  = os.getenv("NOTION_API_KEY", "")
NOTION_VERSION  = os.getenv("NOTION_VERSION", "2025-09-03")
BASE_URL        = "https://api.notion.com/v1"

# ── IDs dos databases de relatórios ──────────────────────────────────────────
# data_source_id → para queries
# database_id    → para criar páginas (parent)

DATABASES = {
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


def _get(path: str) -> dict:
    url = f"{BASE_URL}{path}"
    r = requests.get(url, headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict) -> dict:
    url = f"{BASE_URL}{path}"
    r = requests.post(url, headers=_headers(), json=body, timeout=15)
    r.raise_for_status()
    return r.json()


def _patch(path: str, body: dict) -> dict:
    url = f"{BASE_URL}{path}"
    r = requests.patch(url, headers=_headers(), json=body, timeout=15)
    r.raise_for_status()
    return r.json()


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
    return {"rich_text": [{"text": {"content": texto}}]}


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

def get_relatorios_cliente(
    cliente_key: str,
    limit: int = 30,
    data_inicio: str = None,
    data_fim: str = None,
) -> list[dict]:
    """
    Retorna relatórios de um cliente específico.
    
    Args:
        cliente_key: chave do DATABASES (ex: 'consorcio_sorteado')
        limit: número de registros
        data_inicio: ISO date (ex: '2026-06-01')
        data_fim: ISO date (ex: '2026-06-30')
    
    Returns:
        Lista de dicts com os campos extraídos
    """
    db = DATABASES.get(cliente_key)
    if not db:
        raise ValueError(f"Cliente não encontrado: {cliente_key}. Disponíveis: {list(DATABASES.keys())}")

    filtros = None
    if data_inicio or data_fim:
        date_filter: dict[str, Any] = {}
        if data_inicio and data_fim:
            date_filter = {"on_or_after": data_inicio}
        elif data_inicio:
            date_filter = {"on_or_after": data_inicio}
        elif data_fim:
            date_filter = {"on_or_before": data_fim}
        filtros = {"property": "Data", "date": date_filter}

    pages = query_database(
        data_source_id=db["data_source_id"],
        filtros=filtros,
        sorts=[{"property": "Data", "direction": "descending"}],
        limit=limit,
    )
    return [pagina_para_dict(p) for p in pages]


def publicar_relatorio(
    cliente_key: str,
    titulo: str,
    plataforma: str,
    data: str,
    investimento: float,
    roas: float,
    conversoes: int,
    cliente_nome: str = None,
    conteudo_blocos: list[dict] = None,
) -> dict:
    """
    Publica um novo relatório no database do cliente.
    
    Args:
        cliente_key:    chave do DATABASES (ex: 'consorcio_sorteado')
        titulo:         título da página (ex: 'Consórcio Sorteado · meta · Ontem · 22/06/2026')
        plataforma:     'meta', 'google', 'supabase' etc.
        data:           ISO date (ex: '2026-06-22')
        investimento:   valor investido em R$
        roas:           ROAS médio
        conversoes:     número de conversões/compras
        cliente_nome:   nome do cliente para a propriedade Cliente
        conteudo_blocos: lista de blocos Notion para adicionar no corpo da página
    
    Returns:
        Página criada no Notion
    """
    db = DATABASES.get(cliente_key)
    if not db:
        raise ValueError(f"Cliente não encontrado: {cliente_key}")

    if not db.get("database_id"):
        # Busca o database_id a partir de uma página existente
        pages = query_database(db["data_source_id"], limit=1)
        if pages:
            parent = pages[0].get("parent", {})
            db["database_id"] = parent.get("database_id")

    database_id = db.get("database_id")
    if not database_id:
        raise ValueError(f"Não foi possível obter database_id para {cliente_key}")

    properties = {
        "Título":       prop_titulo(titulo),
        "Plataforma":   prop_select(plataforma),
        "Data":         prop_data(data),
        "Investimento": prop_numero(round(investimento, 2)),
        "ROAS":         prop_numero(round(roas, 2)) if roas is not None else {"number": None},
        "Conversões":   prop_numero(conversoes),
    }
    if cliente_nome:
        properties["Cliente"] = prop_select(cliente_nome)

    page = criar_pagina(database_id, properties)

    if conteudo_blocos:
        adicionar_blocos(page["id"], conteudo_blocos)

    logger.info("Relatório publicado no Notion: %s (%s)", titulo, page["id"])
    return page


def listar_databases_disponiveis() -> list[dict]:
    """Retorna a lista de todos os databases configurados."""
    return [
        {"key": k, "nome": v["nome"], "data_source_id": v["data_source_id"]}
        for k, v in DATABASES.items()
    ]
