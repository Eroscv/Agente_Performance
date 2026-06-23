"""
agent/cache.py

Cache em memória com TTL para consultas frequentes.
Evita chamadas repetidas ao banco + Claude para perguntas idênticas.

TTL padrão: 5 minutos (configurável via CACHE_TTL_SECONDS no .env)
Chave: hash da pergunta normalizada + parâmetros de período/campanha
"""

import hashlib
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

TTL = int(os.getenv("CACHE_TTL_SECONDS", "300"))  # 5 min padrão

_store: dict[str, dict] = {}  # { chave: { "data": ..., "expires_at": float, "hits": int } }


def _chave(pergunta: str, thread_ts: str = "") -> str:
    """Gera chave de cache a partir da pergunta normalizada."""
    texto = pergunta.strip().lower()
    # Ignora thread_ts na chave — mesma pergunta em threads diferentes usa o mesmo cache
    return hashlib.md5(texto.encode()).hexdigest()


def get(pergunta: str) -> Optional[dict]:
    """Retorna resultado cacheado ou None se expirado/inexistente."""
    chave = _chave(pergunta)
    entry = _store.get(chave)
    if not entry:
        return None
    if time.time() > entry["expires_at"]:
        del _store[chave]
        logger.debug("Cache expirado: %s", pergunta[:50])
        return None
    _store[chave]["hits"] += 1
    logger.debug("Cache HIT (%dx): %s", entry["hits"], pergunta[:50])
    return entry["data"]


def set(pergunta: str, resultado: dict) -> None:
    """Armazena resultado no cache."""
    chave = _chave(pergunta)
    _store[chave] = {
        "data":       resultado,
        "expires_at": time.time() + TTL,
        "hits":       0,
        "pergunta":   pergunta[:100],
    }
    logger.debug("Cache SET: %s", pergunta[:50])


def invalidar_tudo() -> int:
    """Limpa todo o cache. Usado após recarregar o banco."""
    n = len(_store)
    _store.clear()
    logger.info("Cache limpo: %d entradas removidas", n)
    return n


def stats() -> dict:
    """Retorna estatísticas do cache."""
    agora = time.time()
    ativos   = sum(1 for e in _store.values() if e["expires_at"] > agora)
    expirados = len(_store) - ativos
    total_hits = sum(e["hits"] for e in _store.values())
    return {
        "entradas_ativas":   ativos,
        "entradas_expiradas": expirados,
        "total_hits":        total_hits,
        "ttl_segundos":      TTL,
    }
