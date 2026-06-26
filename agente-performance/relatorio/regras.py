"""
relatorio/regras.py

Regras de negócio Meta Ads – definidas na reunião com Bianca/Eros.

Constantes e funções puras de cálculo.
Nenhuma dependência de banco ou API aqui.
"""

# ── Fator de imposto ──────────────────────────────────────────────────────────
# Bianca confirmou 1,1215.  (1,1225 foi citado em outro momento — ignorar)
FATOR_IMPOSTO: float = 1.1215


# ── Funções de cálculo ────────────────────────────────────────────────────────

def spend_com_imposto(spend: float) -> float:
    """Investimento Meta ajustado pelo imposto."""
    return round(spend * FATOR_IMPOSTO, 2)


def calcular_roas(receita_shopify: float, spend: float) -> float | None:
    """
    ROAS = Receita Shopify / Investimento com Imposto.

    NÃO usa o ROAS fornecido pelo Meta — recalcula sempre.
    Retorna None se spend == 0.
    """
    investimento = spend_com_imposto(spend)
    if investimento == 0:
        return None
    return round(receita_shopify / investimento, 4)


def calcular_cac(spend: float, novos_clientes: int) -> float | None:
    """
    CAC = Investimento com Imposto / Novos Clientes (vem do Shopify via UTM).

    Retorna None se novos_clientes == 0.
    """
    if novos_clientes == 0:
        return None
    return round(spend_com_imposto(spend) / novos_clientes, 2)


def calcular_cpa(spend: float, num_compras: int) -> float | None:
    """
    CPA (Custo por Compra) = Investimento com Imposto / Número de Compras.

    NÃO usar o valor fornecido pelo Meta — recalcular sempre.
    Retorna None se num_compras == 0.
    """
    if num_compras == 0:
        return None
    return round(spend_com_imposto(spend) / num_compras, 2)


def calcular_ticket_medio(receita: float, num_transacoes: int) -> float | None:
    """
    Ticket Médio = Receita / Número de Transações (dados Shopify).

    Retorna None se num_transacoes == 0.
    """
    if num_transacoes == 0:
        return None
    return round(receita / num_transacoes, 2)


def calcular_pct_budget(spend_anuncio: float, spend_total: float) -> float | None:
    """
    % do budget investido em um anúncio específico.

    Retorna None se spend_total == 0.
    """
    if spend_total == 0:
        return None
    return round((spend_anuncio / spend_total) * 100, 2)
