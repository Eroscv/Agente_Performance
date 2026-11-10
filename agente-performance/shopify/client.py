"""
shopify/client.py

Cliente Shopify para o Agente de Performance Guará.
Busca pedidos reais da loja Source Of (e outros clientes) para alimentar os relatórios.

Funções principais:
  - pedidos_por_sku(data_inicio, data_fim)   → lista de dicts {sku, pedidos, receita}
  - pedidos_por_estado(data_inicio, data_fim) → lista de dicts {estado, pedidos, receita}
  - recompras_por_sku(data_inicio, data_fim)  → lista de dicts {sku, recompras, pct_recompras}
  - assiduidade_clientes(data_inicio, data_fim) → lista de dicts {faixa, clientes, pct}
  - receita_total(data_inicio, data_fim)      → float
  - resumo_periodo(data_inicio, data_fim)     → dict com KPIs consolidados

Multi-loja: use get_client_for(cliente_key) para obter funções contextualizadas
de paginação para qualquer cliente configurado.

Paginação automática (cursor-based). Rate limit: 80 req/s (leaky bucket).
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Iterator

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_STORE   = os.getenv("SHOPIFY_STORE", "source-of-brasil").removesuffix(".myshopify.com")
_TOKEN   = os.getenv("SHOPIFY_TOKEN", "")
_API_VER = "2024-01"
_BASE    = f"https://{_STORE}.myshopify.com/admin/api/{_API_VER}"
_HEADERS = {"X-Shopify-Access-Token": _TOKEN, "Content-Type": "application/json"}

# Mapa de configurações por cliente_key
_CLIENT_CONFIG: dict[str, dict[str, str]] = {
    "source_of": {
        "store_env": "SHOPIFY_STORE",
        "token_env": "SHOPIFY_TOKEN",
        "store_default": "source-of-brasil",
    },
    "pod_kombucha": {
        "store_env": "SHOPIFY_POD_STORE",
        "token_env": "SHOPIFY_POD_TOKEN",
        "store_default": "",
    },
    "botica": {
        "store_env": "SHOPIFY_BOTICA_STORE",
        "token_env": "SHOPIFY_BOTICA_TOKEN",
        "store_default": "",
    },
}


def get_client_for(cliente_key: str):
    """
    Retorna um objeto com _paginar_pedidos e _orders_count_por_cliente
    contextualizados para o cliente informado.

    Uso:
        c = get_client_for("pod_kombucha")
        for pedido in c._paginar_pedidos(data_inicio, data_fim):
            ...
    """
    cfg = _CLIENT_CONFIG.get(cliente_key)
    if not cfg:
        raise ValueError(f"cliente_key '{cliente_key}' não configurado em shopify/client.py")

    store = os.getenv(cfg["store_env"], cfg["store_default"]).removesuffix(".myshopify.com")  # aceita os dois formatos
    token = os.getenv(cfg["token_env"], "")

    if not store:
        raise ValueError(
            f"[Shopify/{cliente_key}] {cfg['store_env']} não definido no .env"
        )
    if not token:
        raise ValueError(
            f"[Shopify/{cliente_key}] {cfg['token_env']} não definido no .env"
        )

    base    = f"https://{store}.myshopify.com/admin/api/{_API_VER}"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    class _ShopifyClient:
        """Instância de cliente Shopify isolada por loja."""

        def _get(self, endpoint: str, params: dict = None):
            url = f"{base}/{endpoint}"
            for _ in range(3):
                r = requests.get(url, headers=headers, params=params or {}, timeout=(5, 30))
                if r.status_code == 429:
                    wait = float(r.headers.get("Retry-After", _RETRY_AFTER))
                    logger.warning("[Shopify/%s] Rate limit — aguardando %.1fs", cliente_key, wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r
            raise RuntimeError(f"[Shopify/{cliente_key}] falha após 3 tentativas em {endpoint}")

        def _paginar_pedidos(
            self,
            data_inicio: date,
            data_fim: date,
            campos: str = _ORDER_FIELDS,
            status: str = "paid",
        ) -> Iterator[dict]:
            """Itera sobre pedidos paginando cursor-based."""
            created_at_min = f"{data_inicio.isoformat()}T00:00:00-03:00"
            created_at_max = f"{data_fim.isoformat()}T23:59:59-03:00"
            params = {
                "limit":            _PAGE_SIZE,
                "status":           "any",
                "financial_status": status,
                "created_at_min":   created_at_min,
                "created_at_max":   created_at_max,
                "fields":           campos,
            }
            page = 0
            total = 0
            while True:
                r = self._get("orders.json", params)
                pedidos = r.json().get("orders", [])
                if not pedidos:
                    break
                page += 1
                total += len(pedidos)
                for pedido in pedidos:
                    yield pedido
                link_header = r.headers.get("Link", "")
                next_url = _extrair_next_link(link_header)
                if not next_url:
                    break
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(next_url).query)
                page_info = qs.get("page_info", [None])[0]
                if not page_info:
                    break
                params = {"limit": _PAGE_SIZE, "page_info": page_info}
            logger.info(
                "[Shopify/%s] Total pedidos lidos: %d (%s → %s)",
                cliente_key, total, data_inicio, data_fim,
            )

        def _orders_count_por_cliente(self, customer_ids: list[int]) -> dict[int, int]:
            """Retorna {customer_id: orders_count} para a lista de IDs."""
            if not customer_ids:
                return {}
            result: dict[int, int] = {}
            BATCH = 250
            for i in range(0, len(customer_ids), BATCH):
                batch = customer_ids[i:i + BATCH]
                ids_str = ",".join(str(c) for c in batch)
                try:
                    r = self._get(
                        "customers.json",
                        {"ids": ids_str, "fields": "id,orders_count", "limit": BATCH},
                    )
                    for c in r.json().get("customers", []):
                        result[int(c["id"])] = int(c.get("orders_count") or 1)
                except Exception:
                    for cid in batch:
                        result[cid] = 1
            return result

    return _ShopifyClient()

# Campos mínimos para reduzir payload
_ORDER_FIELDS = (
    "id,created_at,total_price,subtotal_price,total_discounts,"
    "financial_status,fulfillment_status,note_attributes,"
    "line_items,billing_address,shipping_address,customer"
)

# Limite por página (máx Shopify = 250)
_PAGE_SIZE = 250

# Backoff ao atingir rate limit (429)
_RETRY_AFTER = 2.0


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(endpoint: str, params: dict = None) -> dict:
    """GET com retry automático em 429."""
    url = f"{_BASE}/{endpoint}"
    for tentativa in range(1):  # 1 tentativa — relatórios não podem travar
        r = requests.get(url, headers=_HEADERS, params=params or {}, timeout=(5, 30))
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", _RETRY_AFTER))
            logger.warning("[Shopify] Rate limit — aguardando %.1fs", wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"Shopify: falha após 5 tentativas em {endpoint}")


def _paginar_pedidos(
    data_inicio: date,
    data_fim: date,
    campos: str = _ORDER_FIELDS,
    status: str = "paid",
) -> Iterator[dict]:
    """
    Itera sobre todos os pedidos no período usando cursor-based pagination.
    Yields: dict de cada pedido.
    """
    # Shopify usa ISO 8601 com timezone para filtrar por created_at
    created_at_min = f"{data_inicio.isoformat()}T00:00:00-03:00"
    created_at_max = f"{data_fim.isoformat()}T23:59:59-03:00"

    params = {
        "limit":           _PAGE_SIZE,
        "status":          "any",
        "financial_status": status,
        "created_at_min":  created_at_min,
        "created_at_max":  created_at_max,
        "fields":          campos,
    }

    page = 0
    total = 0
    while True:
        r = _get("orders.json", params)
        pedidos = r.json().get("orders", [])
        if not pedidos:
            break

        page += 1
        total += len(pedidos)
        logger.debug("[Shopify] Página %d — %d pedidos (total: %d)", page, len(pedidos), total)

        for pedido in pedidos:
            yield pedido

        # Cursor para próxima página (Link header)
        link_header = r.headers.get("Link", "")
        next_url = _extrair_next_link(link_header)
        if not next_url:
            break

        # Extrai page_info do next_url para próxima chamada
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(next_url).query)
        page_info = qs.get("page_info", [None])[0]
        if not page_info:
            break

        # Na paginação por cursor, só page_info + limit são aceitos
        params = {"limit": _PAGE_SIZE, "page_info": page_info}

    logger.info("[Shopify] Total pedidos lidos: %d (período: %s → %s)", total, data_inicio, data_fim)


def _extrair_next_link(link_header: str) -> str | None:
    """Extrai URL de 'next' do header Link do Shopify."""
    if not link_header:
        return None
    for parte in link_header.split(","):
        parte = parte.strip()
        if 'rel="next"' in parte:
            url = parte.split(";")[0].strip().strip("<>")
            return url
    return None


def _orders_count_por_cliente(customer_ids: list[int]) -> dict[int, int]:
    """
    Busca orders_count em lote para uma lista de customer_ids.
    Retorna dict {customer_id: orders_count}.
    Faz chamadas em batches de 100 ids (limite da API).
    """
    resultado: dict[int, int] = {}
    BATCH = 100
    for i in range(0, len(customer_ids), BATCH):
        ids_str = ",".join(str(cid) for cid in customer_ids[i:i + BATCH])
        r = _get("customers.json", {"ids": ids_str, "fields": "id,orders_count", "limit": BATCH})
        for c in r.json().get("customers", []):
            resultado[c["id"]] = int(c.get("orders_count") or 1)
    return resultado


# ── Funções públicas ──────────────────────────────────────────────────────────

def pedidos_por_sku(
    data_inicio: date,
    data_fim: date,
) -> list[dict]:
    """
    Retorna pedidos e receita agrupados por SKU (nome do produto).

    Returns:
        Lista de dicts ordenada por pedidos desc:
        [{sku, pedidos, receita}]
    """
    contagem: dict[str, dict] = defaultdict(lambda: {"pedidos": 0, "receita": 0.0})

    for pedido in _paginar_pedidos(data_inicio, data_fim):
        for item in pedido.get("line_items", []):
            nome     = item.get("name") or item.get("title") or "—"
            qtd      = int(item.get("quantity") or 1)
            preco    = float(item.get("price") or 0) * qtd
            contagem[nome]["pedidos"] += qtd
            contagem[nome]["receita"] += preco

    resultado = [
        {"sku": sku, "pedidos": v["pedidos"], "receita": round(v["receita"], 2)}
        for sku, v in contagem.items()
    ]
    resultado.sort(key=lambda x: x["pedidos"], reverse=True)
    logger.info("[Shopify] pedidos_por_sku: %d SKUs encontrados", len(resultado))
    return resultado


def pedidos_por_estado(
    data_inicio: date,
    data_fim: date,
) -> list[dict]:
    """
    Retorna pedidos e receita agrupados por estado (UF).

    Returns:
        Lista de dicts ordenada por pedidos desc:
        [{estado, pedidos, receita}]
    """
    contagem: dict[str, dict] = defaultdict(lambda: {"pedidos": 0, "receita": 0.0})

    for pedido in _paginar_pedidos(data_inicio, data_fim):
        # Prioriza shipping_address, fallback billing_address
        addr  = pedido.get("shipping_address") or pedido.get("billing_address") or {}
        estado = addr.get("province") or "Desconhecido"
        total  = float(pedido.get("total_price") or 0)
        contagem[estado]["pedidos"] += 1
        contagem[estado]["receita"] += total

    resultado = [
        {"estado": estado, "pedidos": v["pedidos"], "receita": round(v["receita"], 2)}
        for estado, v in contagem.items()
    ]
    resultado.sort(key=lambda x: x["pedidos"], reverse=True)
    logger.info("[Shopify] pedidos_por_estado: %d estados encontrados", len(resultado))
    return resultado


def recompras_por_sku(
    data_inicio: date,
    data_fim: date,
) -> list[dict]:
    """
    Retorna recompras agrupadas por SKU — pedidos de clientes que já compraram antes.
    Usa lookup real de orders_count via API de customers.

    Returns:
        Lista de dicts ordenada por recompras desc:
        [{sku, recompras, pct_recompras}]
    """
    # 1ª passagem: coleta todos os pedidos + customer_ids
    pedidos_buffer: list[dict] = []
    customer_ids: set[int]     = set()

    for pedido in _paginar_pedidos(data_inicio, data_fim):
        pedidos_buffer.append(pedido)
        cid = (pedido.get("customer") or {}).get("id")
        if cid:
            customer_ids.add(cid)

    # 2ª passagem: busca orders_count real por cliente
    oc_map = _orders_count_por_cliente(list(customer_ids))

    contagem: dict[str, int] = defaultdict(int)
    total_recompras = 0

    for pedido in pedidos_buffer:
        cid = (pedido.get("customer") or {}).get("id")
        orders_count = oc_map.get(cid, 1) if cid else 1
        if orders_count > 1:  # é recompra
            total_recompras += 1
            for item in pedido.get("line_items", []):
                nome = item.get("name") or item.get("title") or "—"
                qtd  = int(item.get("quantity") or 1)
                contagem[nome] += qtd

    resultado = [
        {
            "sku":           sku,
            "recompras":     qtd,
            "pct_recompras": round(qtd / total_recompras * 100, 1) if total_recompras else 0.0,
        }
        for sku, qtd in contagem.items()
    ]
    resultado.sort(key=lambda x: x["recompras"], reverse=True)
    logger.info(
        "[Shopify] recompras_por_sku: %d SKUs, %d recompras totais",
        len(resultado), total_recompras,
    )
    return resultado


def assiduidade_clientes(
    data_inicio: date,
    data_fim: date,
) -> list[dict]:
    """
    Distribuição de clientes por número de pedidos histórico (assiduidade).
    Usa orders_count real via API de customers (total histórico, não só no período).

    Returns:
        Lista de dicts:
        [{faixa, clientes, pct}]
        Faixas: "1 pedido", "2 pedidos", "3 pedidos", "4-5 pedidos", "6+ pedidos"
    """
    # Coleta customer_ids únicos do período
    customer_ids: set[int] = set()
    for pedido in _paginar_pedidos(data_inicio, data_fim, campos="id,customer"):
        cid = (pedido.get("customer") or {}).get("id")
        if cid:
            customer_ids.add(cid)

    # Busca orders_count real
    oc_map = _orders_count_por_cliente(list(customer_ids))

    faixas = {
        "1 pedido":    0,
        "2 pedidos":   0,
        "3 pedidos":   0,
        "4-5 pedidos": 0,
        "6+ pedidos":  0,
    }
    for n in oc_map.values():
        if n == 1:
            faixas["1 pedido"] += 1
        elif n == 2:
            faixas["2 pedidos"] += 1
        elif n == 3:
            faixas["3 pedidos"] += 1
        elif n <= 5:
            faixas["4-5 pedidos"] += 1
        else:
            faixas["6+ pedidos"] += 1

    total_clientes = sum(faixas.values())
    resultado = [
        {
            "faixa":    faixa,
            "clientes": qtd,
            "pct":      round(qtd / total_clientes * 100, 1) if total_clientes else 0.0,
        }
        for faixa, qtd in faixas.items()
        if qtd > 0
    ]
    logger.info(
        "[Shopify] assiduidade_clientes: %d clientes únicos no período",
        total_clientes,
    )
    return resultado


def metricas_site(
    data_inicio: date,
    data_fim: date,
    cliente_key: str = "source_of",
) -> dict:
    """
    Métricas de site/loja para a tabela 'Métricas de Site' do relatório.

    Calcula diretamente dos pedidos Shopify:
      - receita_total, total_pedidos, ticket_medio
      - novos_clientes, recompradores, taxa_recompra_pct
      - pct_desconto (% da receita que veio com desconto)
      - pedidos_acima_300

    Sessões e funil via ShopifyQL (tabela shopify_sessoes):
      - sessoes, sessoes_checkout, sessoes_compra, taxa_conversao
    """
    receita        = 0.0
    total_pedidos  = 0
    total_desconto = 0.0
    pedidos_300    = 0
    clientes_unicos: set[int] = set()
    pedidos_buffer: list[dict] = []

    for pedido in _paginar_pedidos(
        data_inicio, data_fim,
        campos="id,total_price,total_discounts,customer",
    ):
        total_pedidos  += 1
        preco           = float(pedido.get("total_price") or 0)
        desconto        = float(pedido.get("total_discounts") or 0)
        receita        += preco
        total_desconto += desconto
        if preco > 300:
            pedidos_300 += 1

        cid = (pedido.get("customer") or {}).get("id")
        if cid:
            clientes_unicos.add(cid)
            pedidos_buffer.append(pedido)

    # Lookup orders_count real
    oc_map        = _orders_count_por_cliente(list(clientes_unicos))
    novos         = sum(1 for p in pedidos_buffer if oc_map.get((p.get("customer") or {}).get("id"), 1) == 1)
    recompradores = sum(1 for p in pedidos_buffer if oc_map.get((p.get("customer") or {}).get("id"), 1) > 1)

    ticket_medio     = round(receita / total_pedidos, 2) if total_pedidos else None
    taxa_recompra    = round(recompradores / total_pedidos * 100, 2) if total_pedidos else None
    pct_desconto     = round(total_desconto / receita * 100, 2) if receita else None

    # Sessões e funil: agrega da tabela shopify_sessoes (ShopifyQL)
    sessoes = sessoes_checkout = sessoes_compra = taxa_conversao = None
    try:
        import sqlite3 as _s3
        from pathlib import Path as _Path
        _tabela = f"{cliente_key}_shopify_sessoes"
        _db_map = {
            "source_of":    "data/performance/source_of/shopify.db",
            "pod_kombucha": "data/performance/pod_kombucha/shopify.db",
        }
        _base = _Path(__file__).parent.parent
        _db = _base / _db_map.get(cliente_key, f"data/performance/{cliente_key}/shopify.db")
        if _db.exists():
            _conn = _s3.connect(str(_db))
            _conn.row_factory = _s3.Row
            row = _conn.execute(f"""
                SELECT
                    SUM(sessoes)          AS sessoes,
                    SUM(sessoes_checkout) AS sessoes_checkout,
                    SUM(sessoes_compra)   AS sessoes_compra,
                    AVG(taxa_conversao)   AS taxa_conversao
                FROM {_tabela}
                WHERE data BETWEEN ? AND ?
                  AND (is_bot_suspect = 0 OR is_bot_suspect IS NULL)
            """, (str(data_inicio), str(data_fim))).fetchone()
            _conn.close()
            if row and row["sessoes"]:
                sessoes          = int(row["sessoes"])
                sessoes_checkout = int(row["sessoes_checkout"]) if row["sessoes_checkout"] else None
                sessoes_compra   = int(row["sessoes_compra"])   if row["sessoes_compra"]   else None
                taxa_conversao   = round(float(row["taxa_conversao"]), 6) if row["taxa_conversao"] else None
    except Exception as _e:
        logger.warning("[Shopify] Não foi possível buscar sessões de %s: %s", cliente_key, _e)

    logger.info(
        "[Shopify] metricas_site: %d pedidos | receita R$%.2f | novos %d | recompra %.1f%% | sessões %s",
        total_pedidos, receita, novos, taxa_recompra or 0, sessoes,
    )
    return {
        "receita_total":     round(receita, 2),
        "total_pedidos":     total_pedidos,
        "ticket_medio":      ticket_medio,
        "novos_clientes":    novos,
        "recompradores":     recompradores,
        "taxa_recompra_pct": taxa_recompra,
        "pct_desconto":      pct_desconto,
        "pedidos_acima_300": pedidos_300,
        "total_desconto":    round(total_desconto, 2),
        # Sessões e funil via ShopifyQL (sync_shopify_sessions.py)
        "sessoes":           sessoes,
        "sessoes_checkout":  sessoes_checkout,
        "sessoes_compra":    sessoes_compra,
        "taxa_conversao":    taxa_conversao,
    }


def receita_total(
    data_inicio: date,
    data_fim: date,
) -> float:
    """
    Retorna receita total Shopify no período (soma de total_price dos pedidos pagos).
    """
    total = 0.0
    for pedido in _paginar_pedidos(data_inicio, data_fim, campos="id,total_price"):
        total += float(pedido.get("total_price") or 0)
    logger.info("[Shopify] receita_total: R$ %.2f (período: %s → %s)", total, data_inicio, data_fim)
    return round(total, 2)


def resumo_periodo(
    data_inicio: date,
    data_fim: date,
) -> dict:
    """
    KPIs consolidados do período: receita, pedidos, ticket médio,
    novos clientes, recompradores, taxa de recompra.

    Returns:
        {
            receita_total, total_pedidos, ticket_medio,
            novos_clientes, recompradores, taxa_recompra_pct,
            total_clientes_unicos,
        }
    """
    receita        = 0.0
    total_pedidos  = 0
    clientes_unicos: set[int] = set()
    pedidos_buffer: list[dict] = []

    for pedido in _paginar_pedidos(data_inicio, data_fim):
        total_pedidos += 1
        receita       += float(pedido.get("total_price") or 0)
        cid = (pedido.get("customer") or {}).get("id")
        if cid:
            clientes_unicos.add(cid)
            pedidos_buffer.append(pedido)

    # Busca orders_count real
    oc_map = _orders_count_por_cliente(list(clientes_unicos))

    novos         = sum(1 for p in pedidos_buffer if oc_map.get((p.get("customer") or {}).get("id"), 1) == 1)
    recompradores = sum(1 for p in pedidos_buffer if oc_map.get((p.get("customer") or {}).get("id"), 1) > 1)

    ticket_medio  = round(receita / total_pedidos, 2) if total_pedidos else None
    taxa_recompra = round(recompradores / total_pedidos * 100, 2) if total_pedidos else None

    return {
        "receita_total":         round(receita, 2),
        "total_pedidos":         total_pedidos,
        "ticket_medio":          ticket_medio,
        "novos_clientes":        novos,
        "recompradores":         recompradores,
        "taxa_recompra_pct":     taxa_recompra,
        "total_clientes_unicos": len(clientes_unicos),
    }
