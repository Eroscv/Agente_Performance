"""
relatorio/meta.py

Constrói os blocos de dados para relatórios Meta Ads.

Aplica as regras de negócio definidas na reunião (regras.py):
  - Investimento sempre corrigido pelo fator de imposto (1,1215)
  - ROAS recalculado  (Receita Shopify / Invest. c/ Imposto)
  - CAC  recalculado  (Invest. c/ Imposto / Novos Clientes — Shopify via UTM)
  - CPA  recalculado  (Invest. c/ Imposto / Compras)
  - CPC  NÃO recalculado — usa direto da plataforma Meta
  - Ticket Médio      (Receita / Transações — Shopify)

Exporta:
  - tabela_principal(campanhas, shopify_rows)  → list[dict]
  - alocacao_budget(campanhas)                 → list[dict]
  - top_ads(campanhas, shopify_rows)           → list[dict]
"""

from __future__ import annotations

import logging
from typing import Any

from relatorio.regras import (
    FATOR_IMPOSTO,
    spend_com_imposto,
    calcular_roas,
    calcular_cac,
    calcular_cpa,
    calcular_ticket_medio,
    calcular_pct_budget,
)

logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_brl(v: float | None) -> str:
    """Formata valor em BRL para exibição."""
    if v is None:
        return "—"
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}%"


def _fmt_x(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}x"


# ── 1. Tabela Principal ───────────────────────────────────────────────────────

def tabela_principal(
    campanhas: list[dict],
    shopify_rows: list[dict] | None = None,
) -> list[dict]:
    """
    Campos da tabela principal do relatório semanal Meta:

      Investimento Meta Ads        (spend bruto)
      Investimento + Imposto       (spend × 1,1215)
      Transações Shopify           (do Shopify)
      Receita Shopify              (do Shopify)
      ROAS                         (recalculado)
      Ticket Médio                 (Receita / Transações)
      CPC                          (direto da plataforma — NÃO recalcular)
      Compras (Meta)               (da plataforma)
      CPA                          (recalculado)

    shopify_rows: lista de dicts com chaves:
        campanha, transacoes, receita
    """
    # Indexa Shopify por campanha (pode ser None)
    shopify_map: dict[str, dict] = {}
    for row in (shopify_rows or []):
        key = (row.get("campanha") or "").strip().lower()
        shopify_map[key] = row

    resultado = []
    for c in campanhas:
        nome     = (c.get("campanha") or "").strip()
        spend    = float(c.get("investimento") or 0)
        compras  = int(c.get("compras") or 0)
        cpc      = c.get("cpc_link")          # usa direto — não recalcular

        shop     = shopify_map.get(nome.lower(), {})
        transacoes = int(shop.get("transacoes") or 0)
        receita    = float(shop.get("receita") or 0)

        invest_imp = spend_com_imposto(spend)
        roas       = calcular_roas(receita, spend)
        ticket     = calcular_ticket_medio(receita, transacoes)
        cpa        = calcular_cpa(spend, compras)

        resultado.append({
            "Campanha":                nome,
            "Investimento Meta Ads":   _fmt_brl(spend),
            "Investimento + Imposto":  _fmt_brl(invest_imp),
            "Transações Shopify":      transacoes or "—",
            "Receita Shopify":         _fmt_brl(receita) if receita else "—",
            "ROAS":                    _fmt_x(roas),
            "Ticket Médio":            _fmt_brl(ticket),
            "CPC (plataforma)":        _fmt_brl(float(cpc)) if cpc else "—",
            "Compras (Meta)":          compras or "—",
            "CPA":                     _fmt_brl(cpa),
            # valores numéricos brutos para sorting/gráficos
            "_spend":          spend,
            "_invest_imp":     invest_imp,
            "_roas":           roas,
            "_ticket":         ticket,
            "_cpa":            cpa,
            "_transacoes":     transacoes,
            "_receita":        receita,
        })

    return resultado


# ── 2. Alocação de Budget por Anúncio ────────────────────────────────────────

def alocacao_budget(campanhas: list[dict]) -> list[dict]:
    """
    Gráfico de distribuição do orçamento por anúncio.

    Campos:
      Nome do Anúncio
      Investimento
      % do Investimento Total
    """
    # Filtra linhas que tenham nome de anúncio
    linhas = [c for c in campanhas if c.get("campanha")]
    total  = sum(float(c.get("investimento") or 0) for c in linhas)

    resultado = []
    for c in linhas:
        spend = float(c.get("investimento") or 0)
        pct   = calcular_pct_budget(spend, total)
        resultado.append({
            "Anúncio":           (c.get("campanha") or "").strip(),
            "Investimento":      _fmt_brl(spend),
            "% do Budget":       _fmt_pct(pct),
            "_spend":            spend,
            "_pct":              pct,
        })

    # Ordena por investimento decrescente
    resultado.sort(key=lambda x: x["_spend"], reverse=True)
    return resultado


# ── 3. Top Ads ────────────────────────────────────────────────────────────────

def top_ads(
    campanhas: list[dict],
    shopify_rows: list[dict] | None = None,
) -> list[dict]:
    """
    Anúncios mais investidos com cruzamento Meta ↔ Shopify via UTM.

    Campos por anúncio:
      Nome do Anúncio
      Investimento
      Pedidos               (Shopify via UTM)
      Novos Clientes        (Shopify via UTM)
      Clientes Recorrentes  (Shopify via UTM)
      CAC                   (recalculado: Invest. c/ Imposto / Novos Clientes)
      CPA                   (recalculado: Invest. c/ Imposto / Pedidos)
      ROAS                  (recalculado: Receita / Invest. c/ Imposto)
    """
    shopify_map: dict[str, dict] = {}
    for row in (shopify_rows or []):
        key = (row.get("utm_content") or row.get("campanha") or "").strip().lower()
        shopify_map[key] = row

    resultado = []
    for c in campanhas:
        nome  = (c.get("campanha") or "").strip()
        spend = float(c.get("investimento") or 0)

        shop            = shopify_map.get(nome.lower(), {})
        pedidos         = int(shop.get("pedidos") or 0)
        novos_clientes  = int(shop.get("novos_clientes") or 0)
        recorrentes     = int(shop.get("clientes_recorrentes") or 0)
        receita         = float(shop.get("receita") or 0)

        cac  = calcular_cac(spend, novos_clientes)
        cpa  = calcular_cpa(spend, pedidos)
        roas = calcular_roas(receita, spend)

        resultado.append({
            "Anúncio":             nome,
            "Investimento":        _fmt_brl(spend),
            "Pedidos":             pedidos or "—",
            "Novos Clientes":      novos_clientes or "—",
            "Clientes Recorrentes":recorrentes or "—",
            "CAC":                 _fmt_brl(cac),
            "CPA":                 _fmt_brl(cpa),
            "ROAS":                _fmt_x(roas),
            "_spend":              spend,
            "_cac":                cac,
            "_cpa":                cpa,
            "_roas":               roas,
        })

    # Ordena por investimento decrescente (top ads = mais investidos)
    resultado.sort(key=lambda x: x["_spend"], reverse=True)
    return resultado
