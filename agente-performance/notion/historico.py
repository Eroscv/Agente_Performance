"""
notion/historico.py

Gerencia a página-mãe "Histórico de Relatórios" no Notion.

Estrutura criada:
  📁 Histórico de Relatórios — Source OF
    └── 📅 2026
        └── 📊 Relatório Semanal — 22/06 → 28/06
        └── 📊 Relatório Mensal — Junho 2026
    └── 📅 2025
        └── ...

Estratégia:
  - A página-mãe é criada uma vez e seu ID salvo em .notion_historico_id
  - Sub-páginas de ano são criadas conforme necessário
  - Novos relatórios são criados como filhos da página de ano correspondente
  - Tudo via API REST padrão do Notion (parent.page_id)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from notion.client import (
    DATABASES,
    _get,
    _post,
    _patch,
    prop_titulo,
    query_database,
    criar_pagina,
    adicionar_blocos,
)

logger = logging.getLogger(__name__)

# Arquivo local que persiste os IDs das páginas já criadas
_CACHE_FILE = Path(__file__).parent.parent / "data" / ".notion_historico_ids.json"


# ── Cache local de IDs ────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_cache(data: dict):
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        logger.warning("Não foi possível salvar cache de IDs Notion: %s", e)


# ── Criação de páginas filhas (parent = page_id) ──────────────────────────────

def _criar_subpagina(parent_page_id: str, titulo: str, emoji: str = "📄") -> dict:
    """Cria uma página como filha de outra página (não database)."""
    body = {
        "parent": {"page_id": parent_page_id},
        "icon": {"type": "emoji", "emoji": emoji},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": titulo}}]
            }
        },
    }
    return _post("/pages", body)


def _buscar_subpagina_por_titulo(parent_page_id: str, titulo: str) -> str | None:
    """
    Busca uma subpágina pelo título dentro de uma página pai.
    Usa o endpoint de busca de blocos filhos.
    Retorna o page_id se encontrar, None caso contrário.
    """
    try:
        result = _get(f"/blocks/{parent_page_id}/children?page_size=100")
        for bloco in result.get("results", []):
            if bloco.get("type") == "child_page":
                t = bloco.get("child_page", {}).get("title", "")
                if t.strip() == titulo.strip():
                    return bloco["id"]
    except Exception as e:
        logger.warning("Erro ao buscar subpágina '%s': %s", titulo, e)
    return None


# ── Página-mãe: "Histórico de Relatórios" ────────────────────────────────────

def _obter_ou_criar_pagina_mae(cliente_key: str = "source_of") -> str:
    """
    Retorna o page_id da página-mãe "Histórico de Relatórios".
    Cria se não existir, usando o database source_of como âncora.
    ID é persistido em cache local para evitar recriação.
    """
    cache = _load_cache()
    cache_key = f"pagina_mae_{cliente_key}"

    # Verifica se já temos o ID em cache
    if cache.get(cache_key):
        pid = cache[cache_key]
        # Valida que a página ainda existe
        try:
            _get(f"/pages/{pid}")
            logger.debug("Página-mãe encontrada no cache: %s", pid)
            return pid
        except Exception:
            logger.info("Página-mãe do cache não existe mais — recriando")
            del cache[cache_key]

    # Cria a página-mãe no database source_of
    db_info = DATABASES.get(cliente_key, {})
    db_id   = db_info.get("database_id")

    if not db_id:
        pages = query_database(db_info["data_source_id"], limit=1)
        if pages:
            db_id = pages[0].get("parent", {}).get("database_id")
            db_info["database_id"] = db_id

    if not db_id:
        raise RuntimeError(f"Não foi possível obter database_id para '{cliente_key}'")

    page = criar_pagina(db_id, {"Título": prop_titulo("📁 Histórico de Relatórios — Source OF")})
    pid  = page["id"]

    # Adiciona descrição
    adicionar_blocos(pid, [
        {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content":
                    "Todos os relatórios gerados automaticamente pelo Agente de Performance Guará. "
                    "Organizados por ano e tipo (semanal / mensal)."
                }}],
                "icon": {"type": "emoji", "emoji": "⚙️"},
            }
        },
        {"object": "block", "type": "divider", "divider": {}},
    ])

    cache[cache_key] = pid
    _save_cache(cache)
    logger.info("Página-mãe criada: %s", pid)
    return pid


# ── Página de ano ─────────────────────────────────────────────────────────────

def _obter_ou_criar_pagina_ano(pagina_mae_id: str, ano: int) -> str:
    """
    Retorna o page_id da subpágina do ano dentro da página-mãe.
    Cria se não existir.
    """
    cache     = _load_cache()
    cache_key = f"pagina_ano_{ano}"

    if cache.get(cache_key):
        pid = cache[cache_key]
        try:
            _get(f"/pages/{pid}")
            return pid
        except Exception:
            del cache[cache_key]

    titulo = f"📅 {ano}"

    # Tenta encontrar já existente
    pid_existente = _buscar_subpagina_por_titulo(pagina_mae_id, titulo)
    if pid_existente:
        cache[cache_key] = pid_existente
        _save_cache(cache)
        return pid_existente

    # Cria nova
    page = _criar_subpagina(pagina_mae_id, titulo, emoji="📅")
    pid  = page["id"]
    cache[cache_key] = pid
    _save_cache(cache)
    logger.info("Página de ano %d criada: %s", ano, pid)
    return pid


# ── Função principal ──────────────────────────────────────────────────────────

def criar_relatorio_no_historico(
    titulo: str,
    blocos: list[dict],
    periodo_str: str,
    tipo: str = "semanal",       # "semanal" | "mensal"
    data_ref: date = None,
    cliente_key: str = "source_of",
) -> dict:
    """
    Cria o relatório como subpágina dentro do histórico organizado por ano.

    Estrutura:
      📁 Histórico de Relatórios — Source OF
        └── 📅 2026
              └── 📊 [titulo]

    Args:
        titulo:      título da página do relatório
        blocos:      blocos de conteúdo Notion já montados
        periodo_str: string de período para exibição (ex: "22/06/2026 → 28/06/2026")
        tipo:        "semanal" ou "mensal"
        data_ref:    data de referência para ano (default: hoje)
        cliente_key: chave do cliente Notion

    Returns:
        dict com page_id e url
    """
    if data_ref is None:
        data_ref = date.today()

    ano = data_ref.year

    # 1. Garante página-mãe
    pagina_mae_id = _obter_ou_criar_pagina_mae(cliente_key)

    # 2. Garante página do ano
    pagina_ano_id = _obter_ou_criar_pagina_ano(pagina_mae_id, ano)

    # 3. Cria o relatório como filho do ano
    emoji = "📊" if tipo == "semanal" else "📆"
    page  = _criar_subpagina(pagina_ano_id, titulo, emoji=emoji)
    pid   = page["id"]
    url   = page.get("url", f"https://notion.so/{pid.replace('-', '')}")

    # 4. Adiciona os blocos em lotes de 95
    LOTE = 95
    for i in range(0, len(blocos), LOTE):
        adicionar_blocos(pid, blocos[i:i + LOTE])

    logger.info(
        "[Histórico] Relatório %s criado em 📅 %d → %s",
        tipo, ano, url,
    )
    return {"page_id": pid, "url": url}
