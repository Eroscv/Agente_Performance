"""
agent/registry.py

Registry de clientes e segmentos do Agente de Performance Guará.

Responsabilidades:
  - Ler clientes.json e retornar segmentos ativos (cliente + plataforma)
  - Inferir cliente_key a partir do canal Slack
  - Listar campanhas disponíveis no banco

Separado de engine.py para que OrchestratorAgent, ClientAgents e
GeneralAgent possam importar sem criar dependência circular.
"""
from __future__ import annotations

import json as _json
import logging
import os as _os
from typing import Optional

logger = logging.getLogger(__name__)

# Caminho para clientes.json (raiz do projeto)
_CLIENTES_JSON = _os.path.join(
    _os.path.dirname(_os.path.dirname(__file__)), "clientes.json"
)

# Labels amigáveis para exibição
_PLATAFORMA_LABEL: dict[str, str] = {
    "meta_ads":   "Meta Ads",
    "google_ads": "Google Ads",
}


def carregar_segmentos_ativos() -> list[dict]:
    """
    Lê clientes.json e retorna lista de segmentos ativos.

    Cada item:
        {
          "cliente_key":  str,   # ex: "source_of"
          "plataforma":   str,   # ex: "meta_ads"
          "nome_cliente": str,   # ex: "Source OF"
          "nome_plat":    str,   # ex: "Meta Ads"
          "emoji":        str,   # ex: "🔵"
          "label":        str,   # ex: "Source OF · Meta Ads"
        }

    Exclui shopify (não é plataforma de anúncios) e entradas com ativo=false.
    """
    try:
        with open(_CLIENTES_JSON, encoding="utf-8") as f:
            dados = _json.load(f)
    except Exception as e:
        logger.error("[registry] Falha ao ler clientes.json: %s", e)
        return []

    segmentos = []
    for ck, cfg in dados.get("clientes", {}).items():
        if not cfg.get("ativo", False):
            continue
        nome_cliente = cfg.get("nome", ck)
        emoji        = cfg.get("slack_emoji", "📊")
        for plat, pcfg in cfg.get("plataformas", {}).items():
            if plat == "shopify":
                continue
            if not pcfg.get("ativo", False):
                continue
            nome_plat = _PLATAFORMA_LABEL.get(plat, plat)
            segmentos.append({
                "cliente_key":  ck,
                "plataforma":   plat,
                "nome_cliente": nome_cliente,
                "nome_plat":    nome_plat,
                "emoji":        emoji,
                "label":        f"{nome_cliente} · {nome_plat}",
            })
    return segmentos


def get_contexto_cliente(canal_id: str) -> Optional[str]:
    """
    Infere cliente_key a partir do nome/id do canal Slack.

    Regras (case-insensitive):
      - canal contém 'pod' ou 'kombucha' → 'pod_kombucha'
      - canal contém 'source' ou 'sof'   → 'source_of'
      - senão                            → None (canal genérico)
    """
    if not canal_id:
        return None
    c = canal_id.lower()
    if "pod" in c or "kombucha" in c:
        return "pod_kombucha"
    if "source" in c or "sof" in c:
        return "source_of"
    return None


def get_available_campaigns(
    cliente_key: Optional[str] = None,
    plataforma: Optional[str] = None,
) -> list[str]:
    """
    Retorna campanhas disponíveis no banco, ordenadas por investimento total.

    Args:
        cliente_key: filtra por cliente (ex: 'source_of')
        plataforma:  filtra por plataforma (ex: 'meta_ads')
    """
    from db.connection import query as raw_query

    where_parts = ["campanha IS NOT NULL", "campanha != ''"]
    params: list = []

    if cliente_key:
        where_parts.append("cliente_key = ?")
        params.append(cliente_key)
    if plataforma:
        where_parts.append("plataforma = ?")
        params.append(plataforma)

    where = "WHERE " + " AND ".join(where_parts)
    rows = raw_query(f"""
        SELECT campanha FROM v_performance
        {where}
        GROUP BY campanha
        ORDER BY SUM(investimento) DESC
    """, tuple(params) if params else ())
    return [r["campanha"] for r in rows]


# ── Aliases para compatibilidade com engine.py ────────────────────────────────
# engine.py re-exporta estas funções; código legado importa de lá.
# Novos módulos devem importar diretamente de agent.registry.
_carregar_segmentos_ativos = carregar_segmentos_ativos  # alias privado
