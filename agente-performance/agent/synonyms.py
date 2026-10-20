"""
agent/synonyms.py

Mapeamento de sinônimos e linguagem natural para termos técnicos.
Usado pelo NLP antes de enviar a pergunta ao Claude.

Normaliza expressões do usuário → vocabulário interno do agente.

v2:
  - PERIOD_SYNONYMS agora é gerado dinamicamente (datas corretas para o ano atual)
  - "mês passado" corrigido: retorna o mês calendário anterior, não "hoje - 30 dias"
  - normalizar_pergunta deduplica anotações
"""

import calendar
from datetime import date, timedelta
from agent.tz import hoje_brt

# Sinônimos de métricas → nome canônico
METRIC_SYNONYMS: dict[str, str] = {
    # Investimento / Custo
    "gasto":              "investimento",
    "gastos":             "investimento",
    "gastei":             "investimento",
    "investido":          "investimento",
    "budget":             "investimento",
    "verba":              "investimento",
    "quanto custou":      "investimento",
    "custo total":        "investimento",
    "valor investido":    "investimento",
    "dinheiro gasto":     "investimento",

    # Receita / Retorno
    "retorno":            "roas",
    "receita":            "valor_compras",
    "faturamento":        "valor_compras",
    "faturou":            "valor_compras",
    "gerou":              "valor_compras",
    "ganhou":             "valor_compras",
    "lucrou":             "valor_compras",
    "roi":                "roas",
    "vale a pena":        "roas",
    "tá valendo":         "roas",
    "ta valendo":         "roas",
    "eficiência":         "roas",
    "eficiente":          "roas",
    "rentável":           "roas",
    "rentabilidade":      "roas",

    # Compras
    "vendas":             "compras",
    "vendeu":             "compras",
    "conversões":         "compras",
    "pedidos":            "compras",
    "orders":             "compras",
    "transações":         "compras",
    "comprou":            "compras",

    # Cliques
    "clicou":             "cliques_link",
    "clicks":             "cliques_link",
    "acessos":            "cliques_link",
    "visitas":            "cliques_link",

    # CTR
    "taxa de clique":     "ctr",
    "taxa de cliques":    "ctr",

    # CPC
    "custo por clique":   "cpc",

    # CPM
    "custo por mil":      "cpm",

    # Impressões / Alcance
    "visualizações":      "impressoes",
    "views":              "impressoes",
    "apareceu":           "impressoes",
    "exibido":            "impressoes",
    "pessoas":            "alcance",
    "alcançou":           "alcance",

    # Frequência
    "repetição":          "frequencia",
    "vezes que apareceu": "frequencia",
    "saturação":          "frequencia",

    # Funil
    "carrinho":           "adicoes_carrinho",
    "abandonou":          "checkout_iniciado",
    "finalizou":          "compras",
    "conversão":          "compras",
}

# ── Geração dinâmica de períodos ──────────────────────────────────────────────

def _build_period_synonyms() -> dict[str, str]:
    """
    Gera PERIOD_SYNONYMS dinamicamente com base na data atual.

    Regras:
    - Meses pelo nome: se o mês já passou este ano → ano corrente;
      se ainda não chegou → ano corrente (pois não faz sentido apontar para futuro)
      exceto se o mês for claramente histórico (>6 meses atrás → ano anterior)
    - "mês passado" → primeiro e último dia do mês calendário anterior (correto)
    - "semana passada" → segunda a domingo da semana anterior
    - Trimestres calculados com base no trimestre atual
    """
    hoje = hoje_brt()
    ano  = hoje.year

    # ── Relativos ─────────────────────────────────────────────────────────────
    # Semana: segunda a domingo
    inicio_semana_atual = hoje - timedelta(days=hoje.weekday())
    fim_semana_atual    = inicio_semana_atual + timedelta(days=6)
    inicio_sem_passada  = inicio_semana_atual - timedelta(days=7)
    fim_sem_passada     = inicio_semana_atual - timedelta(days=1)

    # Mês passado (calendário, não -30 dias)
    primeiro_do_mes_atual = hoje.replace(day=1)
    ultimo_mes_fim        = primeiro_do_mes_atual - timedelta(days=1)
    ultimo_mes_inicio     = ultimo_mes_fim.replace(day=1)

    # Trimestres
    trimestre_atual = (hoje.month - 1) // 3 + 1
    trim_map = {
        1: (date(ano, 1,  1), date(ano, 3,  31)),
        2: (date(ano, 4,  1), date(ano, 6,  30)),
        3: (date(ano, 7,  1), date(ano, 9,  30)),
        4: (date(ano, 10, 1), date(ano, 12, 31)),
    }
    trim_anterior_num = trimestre_atual - 1 if trimestre_atual > 1 else 4
    trim_anterior_ano = ano if trimestre_atual > 1 else ano - 1
    trim_ant_map = {
        1: (date(trim_anterior_ano, 1,  1), date(trim_anterior_ano, 3,  31)),
        2: (date(trim_anterior_ano, 4,  1), date(trim_anterior_ano, 6,  30)),
        3: (date(trim_anterior_ano, 7,  1), date(trim_anterior_ano, 9,  30)),
        4: (date(trim_anterior_ano, 10, 1), date(trim_anterior_ano, 12, 31)),
    }

    def _fmt(d: date) -> str:
        return d.isoformat()

    syns: dict[str, str] = {
        # Dias
        "ontem":               f"{_fmt(hoje - timedelta(days=1))} a {_fmt(hoje - timedelta(days=1))}",
        "hoje":                f"{_fmt(hoje)} a {_fmt(hoje)}",
        "anteontem":           f"{_fmt(hoje - timedelta(days=2))} a {_fmt(hoje - timedelta(days=2))}",

        # Semanas
        "essa semana":         f"{_fmt(inicio_semana_atual)} a {_fmt(hoje)}",
        "esta semana":         f"{_fmt(inicio_semana_atual)} a {_fmt(hoje)}",
        "semana atual":        f"{_fmt(inicio_semana_atual)} a {_fmt(hoje)}",
        "semana passada":      f"{_fmt(inicio_sem_passada)} a {_fmt(fim_sem_passada)}",
        "última semana":       f"{_fmt(inicio_sem_passada)} a {_fmt(fim_sem_passada)}",
        "últimos 7 dias":      f"{_fmt(hoje - timedelta(days=7))} a {_fmt(hoje)}",
        "últimos 14 dias":     f"{_fmt(hoje - timedelta(days=14))} a {_fmt(hoje)}",
        "últimos 30 dias":     f"{_fmt(hoje - timedelta(days=30))} a {_fmt(hoje)}",
        "últimos 60 dias":     f"{_fmt(hoje - timedelta(days=60))} a {_fmt(hoje)}",
        "últimos 90 dias":     f"{_fmt(hoje - timedelta(days=90))} a {_fmt(hoje)}",

        # Meses — calendário correto
        "esse mês":            f"{_fmt(hoje.replace(day=1))} a {_fmt(hoje)}",
        "este mês":            f"{_fmt(hoje.replace(day=1))} a {_fmt(hoje)}",
        "mês atual":           f"{_fmt(hoje.replace(day=1))} a {_fmt(hoje)}",
        "mês passado":         f"{_fmt(ultimo_mes_inicio)} a {_fmt(ultimo_mes_fim)}",
        "último mês":          f"{_fmt(ultimo_mes_inicio)} a {_fmt(ultimo_mes_fim)}",

        # Trimestres
        "trimestre atual":     f"{_fmt(trim_map[trimestre_atual][0])} a {_fmt(trim_map[trimestre_atual][1])}",
        "esse trimestre":      f"{_fmt(trim_map[trimestre_atual][0])} a {_fmt(hoje)}",
        "trimestre passado":   f"{_fmt(trim_ant_map[trim_anterior_num][0])} a {_fmt(trim_ant_map[trim_anterior_num][1])}",
        "último trimestre":    f"{_fmt(trim_ant_map[trim_anterior_num][0])} a {_fmt(trim_ant_map[trim_anterior_num][1])}",
        "primeiro trimestre":  f"{_fmt(date(ano, 1, 1))} a {_fmt(date(ano, 3, 31))}",
        "segundo trimestre":   f"{_fmt(date(ano, 4, 1))} a {_fmt(date(ano, 6, 30))}",
        "terceiro trimestre":  f"{_fmt(date(ano, 7, 1))} a {_fmt(date(ano, 9, 30))}",
        "quarto trimestre":    f"{_fmt(date(ano, 10, 1))} a {_fmt(date(ano, 12, 31))}",

        # Anos
        "esse ano":            f"{_fmt(date(ano, 1, 1))} a {_fmt(hoje)}",
        "este ano":            f"{_fmt(date(ano, 1, 1))} a {_fmt(hoje)}",
        "ano atual":           f"{_fmt(date(ano, 1, 1))} a {_fmt(hoje)}",
        "ano passado":         f"{_fmt(date(ano - 1, 1, 1))} a {_fmt(date(ano - 1, 12, 31))}",
        f"em {ano}":           f"{_fmt(date(ano, 1, 1))} a {_fmt(hoje)}",
        f"em {ano - 1}":       f"{_fmt(date(ano - 1, 1, 1))} a {_fmt(date(ano - 1, 12, 31))}",
        f"{ano}":              f"{_fmt(date(ano, 1, 1))} a {_fmt(hoje)}",
        f"{ano - 1}":          f"{_fmt(date(ano - 1, 1, 1))} a {_fmt(date(ano - 1, 12, 31))}",

        # Datas especiais (fixas por ser referência histórica)
        "black friday":        "2025-11-28 a 2025-12-02",
        "natal":               "2025-12-15 a 2025-12-25",
        "natal 2025":          "2025-12-15 a 2025-12-25",
        "black friday 2025":   "2025-11-28 a 2025-12-02",
    }

    # ── Meses pelo nome (dinâmico) ─────────────────────────────────────────────
    # Regra: se o mês ainda não chegou este ano → é do ano corrente (futuro próximo)
    # Se passou este ano → ano corrente. Se passou há >6 meses → ainda ano corrente
    # (o Claude já tem a data atual e vai raciocinar; aqui só damos dica de período)
    NOMES_MESES = [
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho",   "agosto",   "setembro", "outubro", "novembro", "dezembro",
    ]
    for i, nome in enumerate(NOMES_MESES, start=1):
        # Determina o ano mais provável para esse mês
        # Se o mês está 3+ meses no futuro relativo ao atual → provavelmente ano passado
        diff = i - hoje.month
        if diff > 3:
            ano_mes = ano - 1
        else:
            ano_mes = ano

        ultimo_dia = calendar.monthrange(ano_mes, i)[1]
        ini = date(ano_mes, i, 1).isoformat()
        fim = date(ano_mes, i, ultimo_dia).isoformat()

        syns[nome]        = f"{ini} a {fim}"
        syns[f"em {nome}"] = f"{ini} a {fim}"
        # Forma "mês de X"
        syns[f"mês de {nome}"] = f"{ini} a {fim}"
        # Com ano explícito (ambos os anos cobertos)
        for yr in (ano, ano - 1):
            ud = calendar.monthrange(yr, i)[1]
            syns[f"{nome} {yr}"]    = f"{date(yr, i, 1).isoformat()} a {date(yr, i, ud).isoformat()}"
            syns[f"{nome}/{yr}"]    = f"{date(yr, i, 1).isoformat()} a {date(yr, i, ud).isoformat()}"
            syns[f"{nome}/{str(yr)[2:]}"] = f"{date(yr, i, 1).isoformat()} a {date(yr, i, ud).isoformat()}"

    return syns


# Instancia ao importar — reavaliado a cada import (processo vive dias, não semanas)
PERIOD_SYNONYMS: dict[str, str] = _build_period_synonyms()


def periodos_detectados(texto: str) -> list[tuple[str, str]]:
    """
    Detecta sinônimos de período no texto e resolve cada um em (data_inicio, data_fim)
    via PERIOD_SYNONYMS, sem duplicatas.

    Usado tanto por normalizar_pergunta() (pra anotar o [contexto] enviado ao LLM)
    quanto por agent.nlp.interpretar() (pra sobrescrever a data da resposta do LLM
    quando o período já foi resolvido deterministicamente — ver nota em interpretar()
    sobre o LLM ignorar a dica de contexto em casos degenerados, ex: "essa semana"
    quando hoje é segunda-feira).
    """
    t = texto.lower()
    vistos: set[str] = set()
    resultado: list[tuple[str, str]] = []

    # Ordena por comprimento (mais específico primeiro) para evitar que "mês" engula "mês passado"
    for sinonimo in sorted(PERIOD_SYNONYMS.keys(), key=len, reverse=True):
        if sinonimo in t:
            periodo = PERIOD_SYNONYMS[sinonimo]
            if periodo not in vistos:
                partes = periodo.split(" a ")
                if len(partes) == 2:
                    resultado.append((partes[0].strip(), partes[1].strip()))
                vistos.add(periodo)
            # Remove o sinonimo do texto processado para evitar matches parciais sobrepostos
            t = t.replace(sinonimo, " ")

    return resultado


def normalizar_pergunta(texto: str) -> str:
    """
    Enriquece a pergunta com anotações de sinônimos para o Claude.

    Deduplicação completa:
    - Mesma canonical de métrica: aparece UMA vez, no formato "metrica=canonical"
    - Mesmo período: aparece UMA vez, com o período resolvido
    - Preserva o texto original
    """
    t = texto.lower()
    anotacoes: list[str] = []
    metricas_vistas: set[str] = set()   # canonical de métrica já anotada

    # Métricas: anota "metrica=canonical" apenas uma vez por canonical
    for sinonimo, canonico in METRIC_SYNONYMS.items():
        if sinonimo in t and canonico not in metricas_vistas:
            anotacoes.append(f"metrica={canonico}")
            metricas_vistas.add(canonico)

    # Períodos: anota "periodo=YYYY-MM-DD a YYYY-MM-DD" apenas uma vez por período resolvido
    for ini, fim in periodos_detectados(texto):
        anotacoes.append(f"periodo={ini} a {fim}")

    if anotacoes:
        return f"{texto} [contexto: {', '.join(anotacoes)}]"
    return texto
