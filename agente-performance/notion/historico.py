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

import requests

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

def _obter_ou_criar_pagina_ano(pagina_mae_id: str, ano: int, cliente_key: str = "source_of") -> str:
    """
    Retorna o page_id da subpágina do ano dentro da página-mãe.
    Cria se não existir.
    """
    cache     = _load_cache()
    cache_key = f"pagina_ano_{cliente_key}_{ano}"

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


# ── Cache por período ───────────────────────────────────────────────────────

def _cache_key_relatorio(cliente_key: str, tipo: str, data_inicio: str, data_fim: str) -> str:
    """Gera chave de cache única por cliente + tipo + período."""
    return f"relatorio_{cliente_key}_{tipo}_{data_inicio}_{data_fim}"


def _buscar_relatorio_em_cache(
    cliente_key: str,
    tipo: str,
    data_inicio: str,
    data_fim: str,
) -> str | None:
    """Retorna page_id se já existe um relatório para esse período no cache."""
    cache = _load_cache()
    key   = _cache_key_relatorio(cliente_key, tipo, data_inicio, data_fim)
    pid   = cache.get(key)
    if not pid:
        return None
    # Valida que a página ainda existe no Notion
    try:
        _get(f"/pages/{pid}")
        return pid
    except Exception:
        logger.info("Relatório em cache não existe mais no Notion: %s", pid)
        cache.pop(key, None)
        _save_cache(cache)
        return None


def _salvar_relatorio_em_cache(
    cliente_key: str,
    tipo: str,
    data_inicio: str,
    data_fim: str,
    page_id: str,
):
    """Salva o page_id do relatório no cache local."""
    cache = _load_cache()
    key   = _cache_key_relatorio(cliente_key, tipo, data_inicio, data_fim)
    cache[key] = page_id
    _save_cache(cache)


def _buscar_relatorio_existente_na_api(
    pagina_ano_id: str,
    titulo: str,
) -> str | None:
    """
    Busca na API do Notion se já existe uma subpágina com esse título
    dentro da página do ano.

    Complementa o cache local: cobre o caso onde o cache foi apagado
    mas a página ainda existe no Notion.
    """
    try:
        result = _get(f"/blocks/{pagina_ano_id}/children?page_size=100")
        for bloco in result.get("results", []):
            if bloco.get("type") == "child_page":
                t = bloco.get("child_page", {}).get("title", "")
                if t.strip() == titulo.strip():
                    logger.debug("[Histórico] Página encontrada na API: '%s' → %s", titulo, bloco["id"])
                    return bloco["id"]
    except Exception as e:
        logger.warning("[Histórico] Falha ao buscar páginas existentes na API: %s", e)
    return None


def _limpar_blocos_pagina(page_id: str):
    """
    Remove todos os blocos de uma página existente.
    Usa DELETE /blocks/{block_id} para cada bloco filho.
    Necessário para reutilizar a página com conteúdo atualizado.
    """
    from notion.client import NOTION_API_KEY, NOTION_VERSION
    BASE_URL = "https://api.notion.com/v1"
    headers  = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type":   "application/json",
    }

    deletados = 0
    cursor    = None

    while True:
        url = f"{BASE_URL}/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"

        resp   = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data   = resp.json()
        blocos = data.get("results", [])

        for bloco in blocos:
            bid = bloco["id"]
            r   = requests.delete(f"{BASE_URL}/blocks/{bid}", headers=headers, timeout=30)
            if r.status_code == 200:
                deletados += 1
            else:
                logger.warning("Falha ao deletar bloco %s: %s", bid, r.status_code)

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    logger.info("[Cache] %d blocos removidos da página %s", deletados, page_id)


# ── Função principal ──────────────────────────────────────────────────────────


def _buscar_relatorio_periodo_anterior(
    pagina_ano_id: str,
    cliente_key: str,
    tipo: str,
    data_inicio: str,
    data_fim: str,
) -> str | None:
    """
    Tenta encontrar o relatório do período anterior ao fornecido.
    Usa o cache local para localizar page_ids por tipo e período.
    Retorna page_id se encontrar, None caso contrário.
    """
    from datetime import date, timedelta
    try:
        di = date.fromisoformat(data_inicio) if data_inicio else None
        df = date.fromisoformat(data_fim)    if data_fim    else None
        if not di or not df:
            return None

        duracao = (df - di).days + 1
        ant_df  = di - timedelta(days=1)
        ant_di  = ant_df - timedelta(days=duracao - 1)

        cache = _load_cache()
        key   = _cache_key_relatorio(cliente_key, tipo, ant_di.isoformat(), ant_df.isoformat())
        pid   = cache.get(key)
        if pid:
            try:
                _get(f"/pages/{pid}")
                return pid
            except Exception:
                pass
    except Exception as e:
        logger.debug("[Histórico] Falha ao buscar relatório anterior: %s", e)
    return None


def _adicionar_link_relatorio(page_id: str, texto: str, url_destino: str, page_id_destino: str):
    """Adiciona um bloco com link para outro relatório em uma página existente."""
    try:
        # Usa mention para criar link interno do Notion
        bloco = {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": f"{texto}: "}},
                    {
                        "type": "mention",
                        "mention": {
                            "type": "page",
                            "page": {"id": page_id_destino},
                        },
                    },
                ]
            }
        }
        adicionar_blocos(page_id, [bloco])
        logger.info("[Histórico] Link adicionado em %s → %s", page_id, page_id_destino)
    except Exception as e:
        logger.warning("[Histórico] Falha ao adicionar link de relatório: %s", e)


def criar_relatorio_no_historico(
    titulo: str,
    blocos: list[dict],
    periodo_str: str,
    tipo: str = "semanal",
    data_ref: date = None,
    data_inicio: date = None,
    data_fim: date = None,
    cliente_key: str = "source_of",
) -> dict:
    """
    Cria (ou atualiza) o relatório como subpágina dentro do histórico.

    Estrutura:
      📁 Histórico de Relatórios — Source OF
        └── 📅 2026
              └── 📊 [titulo]  ← reutilizado se mesmo período

    Comportamento de cache:
      - Se já existe um relatório para esse período:
          limpa os blocos antigos e reescreve com dados novos
      - Se não existe: cria página nova e salva ID no cache

    Returns:
        dict com page_id, url e cache_hit (bool)
    """
    if data_ref is None:
        data_ref = data_fim or date.today()

    ano = data_ref.year

    # Chaves de período para o cache
    di_str = data_inicio.isoformat() if data_inicio else ""
    df_str = data_fim.isoformat()    if data_fim    else ""

    # 1. Verifica cache local: já existe relatório para esse período?
    pid_existente = _buscar_relatorio_em_cache(cliente_key, tipo, di_str, df_str)
    cache_hit     = pid_existente is not None

    if cache_hit:
        logger.info("[Cache] Relatório já existe para %s %s→%s — atualizando", tipo, di_str, df_str)
        pid = pid_existente
        _limpar_blocos_pagina(pid)
        url = f"https://notion.so/{pid.replace('-', '')}"

    else:
        # 2. Garante página-mãe e página do ano
        pagina_mae_id = _obter_ou_criar_pagina_mae(cliente_key)
        pagina_ano_id = _obter_ou_criar_pagina_ano(pagina_mae_id, ano, cliente_key)

        # 3. Verifica na API do Notion (cobre caso de cache apagado)
        emoji = "📊" if tipo == "semanal" else "📆"
        pid_api = _buscar_relatorio_existente_na_api(pagina_ano_id, titulo)
        if pid_api:
            logger.info("[Histórico] Página encontrada na API (cache miss) — reaproveitando: %s", pid_api)
            pid       = pid_api
            cache_hit = True
            _salvar_relatorio_em_cache(cliente_key, tipo, di_str, df_str, pid)
            _limpar_blocos_pagina(pid)
            url = f"https://notion.so/{pid.replace('-', '')}"
        else:
            # 4. Cria subpágina nova
            page = _criar_subpagina(pagina_ano_id, titulo, emoji=emoji)
            pid  = page["id"]
            url  = page.get("url", f"https://notion.so/{pid.replace('-', '')}")
            _salvar_relatorio_em_cache(cliente_key, tipo, di_str, df_str, pid)

    # 4. Escreve blocos (sempre — seja página nova ou reutilizada)
    LOTE = 95
    for i in range(0, len(blocos), LOTE):
        adicionar_blocos(pid, blocos[i:i + LOTE])

    # 5. Links bidirecionais com relatório anterior (apenas para páginas novas)
    if not cache_hit and di_str and df_str:
        try:
            # Obtém pagina_ano_id caso ainda não definido (path cache_hit=False já tem)
            _pagina_ano_id = locals().get("pagina_ano_id")
            pid_anterior = _buscar_relatorio_periodo_anterior(
                _pagina_ano_id or "", cliente_key, tipo, di_str, df_str
            )
            if pid_anterior:
                # Adiciona link "Ver relatório anterior" na nova página
                url_anterior = f"https://notion.so/{pid_anterior.replace('-', '')}"
                _adicionar_link_relatorio(pid, "📌 Ver relatório anterior", url_anterior, pid_anterior)
                # Adiciona link "Ver relatório seguinte" na página anterior
                _adicionar_link_relatorio(pid_anterior, "📌 Ver relatório seguinte", url, pid)
                logger.info("[Histórico] Links bidirecionais criados: %s ↔ %s", pid_anterior, pid)
        except Exception as e:
            logger.warning("[Histórico] Falha ao criar links de relatórios: %s", e)

    acao = "atualizado" if cache_hit else "criado"
    logger.info("[Histórico] Relatório %s %s em 📅 %d → %s", tipo, acao, ano, url)

    return {"page_id": pid, "url": url, "cache_hit": cache_hit}
