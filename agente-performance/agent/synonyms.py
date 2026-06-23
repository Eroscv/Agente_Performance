"""
agent/synonyms.py

Mapeamento de sinônimos e linguagem natural para termos técnicos.
Usado pelo NLP antes de enviar a pergunta ao Claude.

Normaliza expressões do usuário → vocabulário interno do agente.
"""

# Sinônimos de métricas → nome canônico
METRIC_SYNONYMS: dict[str, str] = {
    # Investimento / Custo
    "gasto":            "investimento",
    "gastos":           "investimento",
    "gastei":           "investimento",
    "investido":        "investimento",
    "budget":           "investimento",
    "verba":            "investimento",
    "quanto custou":    "investimento",
    "custo total":      "investimento",
    "valor investido":  "investimento",
    "dinheiro gasto":   "investimento",

    # Receita / Retorno
    "retorno":          "roas",
    "receita":          "valor_compras",
    "faturamento":      "valor_compras",
    "faturou":          "valor_compras",
    "gerou":            "valor_compras",
    "ganhou":           "valor_compras",
    "lucrou":           "valor_compras",
    "roi":              "roas",
    "vale a pena":      "roas",
    "tá valendo":       "roas",
    "ta valendo":       "roas",
    "eficiência":       "roas",
    "eficiente":        "roas",
    "rentável":         "roas",
    "rentabilidade":    "roas",

    # Compras
    "vendas":           "compras",
    "vendeu":           "compras",
    "conversões":       "compras",
    "pedidos":          "compras",
    "orders":           "compras",
    "transações":       "compras",
    "comprou":          "compras",

    # Cliques
    "clicou":           "cliques_link",
    "cliques":          "cliques_link",
    "clicks":           "cliques_link",
    "acessos":          "cliques_link",
    "visitas":          "cliques_link",

    # CTR
    "taxa de clique":   "ctr",
    "taxa de cliques":  "ctr",
    "engajamento":      "ctr",

    # CPC
    "custo por clique": "cpc",
    "cpc":              "cpc",

    # CPM
    "custo por mil":    "cpm",
    "cpm":              "cpm",

    # Impressões / Alcance
    "visualizações":    "impressoes",
    "views":            "impressoes",
    "apareceu":         "impressoes",
    "exibido":          "impressoes",
    "pessoas":          "alcance",
    "alcançou":         "alcance",

    # Frequência
    "repetição":        "frequencia",
    "vezes que apareceu":"frequencia",
    "saturação":        "frequencia",

    # Funil
    "carrinho":         "adicoes_carrinho",
    "abandonou":        "checkout_iniciado",
    "finalizou":        "compras",
    "conversão":        "compras",
}

# Sinônimos de períodos → expressão normalizada
PERIOD_SYNONYMS: dict[str, str] = {
    "ontem":                "últimos 1 dias",
    "hoje":                 "últimos 0 dias",
    "essa semana":          "últimos 7 dias",
    "esta semana":          "últimos 7 dias",
    "semana atual":         "últimos 7 dias",
    "semana passada":       "últimos 7 dias",
    "última semana":        "últimos 7 dias",
    "esse mês":             "mês atual",
    "este mês":             "mês atual",
    "mês atual":            "mês atual",
    "mês passado":          "últimos 30 dias",
    "último mês":           "últimos 30 dias",
    "últimos 30 dias":      "últimos 30 dias",
    "últimos 7 dias":       "últimos 7 dias",
    "últimos 14 dias":      "últimos 14 dias",
    "últimos 90 dias":      "últimos 90 dias",
    "trimestre":            "últimos 90 dias",
    "último trimestre":     "últimos 90 dias",
    "segundo trimestre":    "2026-04-01 a 2026-06-30",
    "primeiro trimestre":   "2026-01-01 a 2026-03-31",
    "black friday":         "2025-11-28 a 2025-12-02",
    "natal":                "2025-12-15 a 2025-12-25",
    "ano passado":          "2025-01-01 a 2025-12-31",
    "em 2025":              "2025-01-01 a 2025-12-31",
    "em 2026":              "2026-01-01 a 2026-12-31",
    "janeiro":              "2026-01-01 a 2026-01-31",
    "fevereiro":            "2026-02-01 a 2026-02-28",
    "março":                "2026-03-01 a 2026-03-31",
    "abril":                "2026-04-01 a 2026-04-30",
    "maio":                 "2026-05-01 a 2026-05-31",
    "junho":                "2026-06-01 a 2026-06-30",
    "julho":                "2025-07-01 a 2025-07-31",
    "agosto":               "2025-08-01 a 2025-08-31",
    "setembro":             "2025-09-01 a 2025-09-30",
    "outubro":              "2025-10-01 a 2025-10-31",
    "novembro":             "2025-11-01 a 2025-11-30",
    "dezembro":             "2025-12-01 a 2025-12-31",
    "em maio":              "2026-05-01 a 2026-05-31",
    "em junho":             "2026-06-01 a 2026-06-30",
    "em novembro":          "2025-11-01 a 2025-11-30",
    "em dezembro":          "2025-12-01 a 2025-12-31",
}

def normalizar_pergunta(texto: str) -> str:
    """
    Substitui sinônimos conhecidos no texto por termos canônicos.
    Preserva o texto original mas enriquece com contexto técnico.
    """
    t = texto.lower()
    anotacoes = []

    for sinonimo, canonico in METRIC_SYNONYMS.items():
        if sinonimo in t:
            anotacoes.append(f"{sinonimo}={canonico}")

    for sinonimo, periodo in PERIOD_SYNONYMS.items():
        if sinonimo in t:
            anotacoes.append(f"periodo={periodo}")

    if anotacoes:
        return f"{texto} [contexto: {', '.join(anotacoes)}]"
    return texto
