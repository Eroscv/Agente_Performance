"""
notion/relatorio.py

Gera relatórios de campanhas como páginas Notion formatadas.
Converte dados do SQLite em blocos Notion ricos: tabelas, métricas, insights.

Funções principais:
  - publicar_relatorio_campanha(resultado, pergunta) → (page_id, url)
"""

import logging
from datetime import date
from typing import Any, Optional

from notion.client import (
    criar_pagina, adicionar_blocos, buscar,
    prop_titulo, prop_texto, prop_numero, prop_select, prop_data,
    DATABASES,
)

logger = logging.getLogger(__name__)

from utils.formatters import fmt_brl as _fmt_brl, fmt_n as _fmt_num, fmt_pct as _fmt_pct, fmt_x as _fmt_x
from notion.blocks import h2 as bloco_heading2, h3 as bloco_heading3, paragrafo as bloco_paragrafo
from notion.blocks import divider as bloco_divisor, callout as bloco_callout, bullet as bloco_bullet
from notion.blocks import toggle as bloco_toggle, codigo as bloco_codigo





# ── Tabela em código (Notion não tem tabela nativa simples) ──────────────────

def _tabela_como_codigo(dados: list[dict], colunas: list[str] = None, max_rows: int = 30) -> str:
    """Gera tabela ASCII para exibir em bloco de código no Notion."""
    if not dados:
        return "Nenhum dado encontrado."

    if colunas is None:
        colunas = list(dados[0].keys())

    # Larguras máximas
    larguras = {c: len(c) for c in colunas}
    for row in dados[:max_rows]:
        for c in colunas:
            v = str(row.get(c, "—") or "—")
            larguras[c] = max(larguras[c], len(v))

    sep = "┼".join("─" * (larguras[c] + 2) for c in colunas)
    header = "│".join(f" {c:<{larguras[c]}} " for c in colunas)
    linhas = [header, sep]
    for row in dados[:max_rows]:
        linha = "│".join(f" {str(row.get(c,'—') or '—'):<{larguras[c]}} " for c in colunas)
        linhas.append(linha)

    if len(dados) > max_rows:
        linhas.append(f"... (+{len(dados) - max_rows} linhas omitidas)")

    return "\n".join(linhas)


# ── Gerador de blocos por intent ──────────────────────────────────────────────

def _blocos_resumo_periodo(dados: dict, meta: dict) -> list[dict]:
    """Blocos para resumo_periodo (dict com totais)."""
    blocos = []
    di = meta.get("data_inicio", "")
    df = meta.get("data_fim", "")

    blocos.append(bloco_heading2(f"📊 Período: {di} → {df}"))
    blocos.append(bloco_divisor())

    # Métricas principais em bullets
    blocos.append(bloco_heading3("Métricas Gerais"))
    metricas = [
        ("💰 Investimento total",  _fmt_brl(dados.get("investimento_total"))),
        ("🛒 Receita total",       _fmt_brl(dados.get("receita_total"))),
        ("📦 Compras totais",      _fmt_num(dados.get("compras_total"))),
        ("📈 ROAS médio",          _fmt_num(dados.get("roas_medio"), 2)),
        ("👁️  Impressões",         _fmt_num(dados.get("impressoes_total"))),
        ("🎯 Alcance",             _fmt_num(dados.get("alcance_total"))),
        ("🖱️  Cliques",            _fmt_num(dados.get("cliques_total"))),
        ("📊 CTR médio",           _fmt_pct(dados.get("ctr_medio"))),
        ("💵 CPC médio",           _fmt_brl(dados.get("cpc_medio"))),
        ("🏪 Campanhas ativas",    str(dados.get("campanhas_ativas", "—"))),
    ]
    for label, valor in metricas:
        if valor and valor != "—":
            blocos.append(bloco_bullet(f"{label}: {valor}"))

    return blocos


def _blocos_lista_campanhas(dados: list[dict], meta: dict) -> list[dict]:
    """Blocos para lista ou ranking de campanhas."""
    blocos = []
    intent = meta.get("intent", "")
    titulo = "🏆 Ranking de Campanhas" if "top" in intent or "ranking" in intent else "📋 Campanhas"
    blocos.append(bloco_heading2(titulo))
    blocos.append(bloco_divisor())

    if not dados:
        blocos.append(bloco_paragrafo("Nenhum dado encontrado."))
        return blocos

    # Colunas relevantes para exibição
    colunas_pref = ["campanha", "roas_medio", "investimento", "receita", "compras",
                    "ctr_medio", "cpc_medio", "impressoes"]
    colunas = [c for c in colunas_pref if c in dados[0]]
    if not colunas:
        colunas = list(dados[0].keys())

    tabela = _tabela_como_codigo(dados, colunas, max_rows=20)
    blocos.append(bloco_codigo(tabela))

    return blocos


def _blocos_performance(dados: list[dict], meta: dict) -> list[dict]:
    """Blocos para série temporal de campanha."""
    blocos = []
    campanha = meta.get("campanha", "Campanha")
    blocos.append(bloco_heading2(f"📈 Performance: {campanha}"))
    blocos.append(bloco_divisor())

    if not dados:
        blocos.append(bloco_paragrafo("Nenhum dado encontrado para o período."))
        return blocos

    # Resumo agregado
    total_invest = sum(float(r.get("investimento") or 0) for r in dados)
    total_receita = sum(float(r.get("receita") or 0) for r in dados)
    total_compras = sum(float(r.get("compras") or 0) for r in dados)
    roas_medio = total_receita / total_invest if total_invest else None

    blocos.append(bloco_heading3("Resumo do Período"))
    blocos += [
        bloco_bullet(f"💰 Investimento: {_fmt_brl(total_invest)}"),
        bloco_bullet(f"🛒 Receita: {_fmt_brl(total_receita)}"),
        bloco_bullet(f"📦 Compras: {_fmt_num(total_compras)}"),
        bloco_bullet(f"📈 ROAS: {_fmt_num(roas_medio, 2)}"),
    ]

    # Série diária em toggle
    colunas = ["data", "investimento", "receita", "compras", "roas", "ctr", "cpc"]
    colunas = [c for c in colunas if c in (dados[0] if dados else {})]
    tabela = _tabela_como_codigo(dados, colunas, max_rows=31)
    blocos.append(bloco_toggle(f"📅 Série diária ({len(dados)} dias)", [bloco_codigo(tabela)]))

    return blocos


def _blocos_funil(dados: dict, meta: dict) -> list[dict]:
    """Blocos para métricas de funil."""
    blocos = []
    campanha = meta.get("campanha", "Geral")
    blocos.append(bloco_heading2(f"🔽 Funil de Conversão — {campanha}"))
    blocos.append(bloco_divisor())

    if not dados:
        blocos.append(bloco_paragrafo("Nenhum dado encontrado."))
        return blocos

    imp    = float(dados.get("impressoes") or 0)
    cli    = float(dados.get("cliques") or 0)
    carr   = float(dados.get("adicoes_carrinho") or 0)
    check  = float(dados.get("checkout_iniciado") or 0)
    comp   = float(dados.get("compras") or 0)

    def taxa(a, b): return f"{a/b*100:.1f}%" if b else "—"

    etapas = [
        ("👁️  Impressões",          _fmt_num(imp),   ""),
        ("🖱️  Cliques",             _fmt_num(cli),   f"CTR: {taxa(cli, imp)}"),
        ("🛒 Add ao Carrinho",      _fmt_num(carr),  f"de cliques: {taxa(carr, cli)}"),
        ("💳 Checkout Iniciado",    _fmt_num(check), f"de carrinhos: {taxa(check, carr)}"),
        ("✅ Compras",              _fmt_num(comp),  f"de checkout: {taxa(comp, check)}"),
    ]
    for label, valor, taxa_txt in etapas:
        txt = f"{label}: {valor}"
        if taxa_txt:
            txt += f"  ({taxa_txt})"
        blocos.append(bloco_bullet(txt))

    blocos.append(bloco_paragrafo(""))
    blocos.append(bloco_bullet(f"💰 Investimento: {_fmt_brl(dados.get('investimento'))}"))
    blocos.append(bloco_bullet(f"🛒 Receita: {_fmt_brl(dados.get('receita'))}"))

    return blocos


def _blocos_alertas(dados: list[dict], meta: dict) -> list[dict]:
    """Blocos para alertas de ROAS."""
    blocos = []
    blocos.append(bloco_heading2("🚨 Alertas de Performance"))
    blocos.append(bloco_divisor())

    if not dados:
        blocos.append(bloco_callout("Nenhuma campanha com ROAS problemático encontrada! ✅", "✅"))
        return blocos

    for a in dados:
        tipo = a.get("tipo_alerta", "")
        roas = a.get("roas_medio")
        invest = a.get("investimento", 0)
        emoji = "🔴" if tipo == "sem_conversao" else "🟡"
        texto = (
            f"{emoji} {a['campanha']}\n"
            f"   ROAS: {_fmt_num(roas, 2) if roas else 'sem conversão'} | "
            f"Investimento: {_fmt_brl(invest)} | "
            f"Última data: {a.get('ultima_data','—')}"
        )
        blocos.append(bloco_paragrafo(texto))

    return blocos


# ── Função principal ──────────────────────────────────────────────────────────

def publicar_relatorio_consulta(
    resultado:   dict,
    pergunta:    str,
    cliente_key: str = None,
) -> tuple[str, str]:
    """
    Gera um relatório no Notion a partir do resultado de uma consulta.
    
    Cria a página no database correto (se cliente_key fornecido) ou
    em uma página raiz acessível pela integração.
    
    Args:
        resultado:   dict retornado por engine.consultar()
        pergunta:    pergunta original do usuário
        cliente_key: chave do DATABASES (ex: 'consorcio_sorteado') — opcional
    
    Returns:
        (page_id, url) da página criada
    """
    meta   = resultado.get("meta", {})
    dados  = resultado.get("dados", [])
    intent = meta.get("intent", "relatorio")
    di     = meta.get("data_inicio", "")
    df     = meta.get("data_fim", "")
    hoje   = date.today().strftime("%d/%m/%Y")

    # Título da página
    partes = ["📊 Source OF — Meta Ads"]
    if meta.get("campanha"):
        partes.append(meta["campanha"])
    periodo = f"{di} → {df}" if di and df else hoje
    partes.append(periodo)
    titulo_pagina = " · ".join(partes)

    # Blocos de conteúdo
    blocos: list[dict] = []
    blocos.append(bloco_callout(
        f"Gerado automaticamente pelo Agente de Performance Guará\n"
        f"Cliente: Source OF · Plataforma: Meta Ads\n"
        f"Pergunta: \"{pergunta}\"\n"
        f"Data de geração: {date.today().isoformat()}",
        emoji="⚙️",
    ))
    blocos.append(bloco_divisor())

    # Gera seções baseadas no intent
    if isinstance(dados, dict):
        # Funil ou resumo_periodo
        if intent == "funil":
            blocos += _blocos_funil(dados, meta)
        else:
            blocos += _blocos_resumo_periodo(dados, meta)
    elif isinstance(dados, list):
        if intent in ("performance_campanha", "evolucao_diaria"):
            blocos += _blocos_performance(dados, meta)
        elif intent in ("alertas_roas",):
            blocos += _blocos_alertas(dados, meta)
        else:
            blocos += _blocos_lista_campanhas(dados, meta)

    # dados_extra (intenções secundárias)
    for extra_intent, extra_dados in (resultado.get("dados_extra") or {}).items():
        if extra_dados:
            blocos.append(bloco_divisor())
            blocos.append(bloco_heading2(f"Dados complementares — {extra_intent}"))
            if isinstance(extra_dados, list) and extra_dados:
                colunas = list(extra_dados[0].keys())[:8]
                blocos.append(bloco_codigo(_tabela_como_codigo(extra_dados, colunas)))
            elif isinstance(extra_dados, dict):
                for k, v in extra_dados.items():
                    blocos.append(bloco_bullet(f"{k}: {v}"))

    blocos.append(bloco_divisor())
    blocos.append(bloco_paragrafo(
        f"Fonte: Source OF · Meta Ads · Agente de Performance Guará · {date.today().isoformat()}"
    ))

    # Decide onde criar a página
    # Padrão: source_of (todos os dados do banco são Source OF)
    if not cliente_key:
        cliente_key = "source_of"

    if cliente_key in DATABASES:
        db_info = DATABASES[cliente_key]
        db_id   = db_info.get("database_id")

        # Resolve database_id se ainda não temos
        if not db_id:
            from notion.client import query_database
            pages = query_database(db_info["data_source_id"], limit=1)
            if pages:
                parent = pages[0].get("parent", {})
                db_id  = parent.get("database_id")
                db_info["database_id"] = db_id

        if db_id:
            properties = {
                "Título": prop_titulo(titulo_pagina),
            }
            if di:
                properties["Data"] = prop_data(di, df or None)

            invest_total = None
            if isinstance(dados, dict):
                invest_total = dados.get("investimento_total") or dados.get("investimento")
            elif isinstance(dados, list) and dados:
                invest_total = sum(float(r.get("investimento") or 0) for r in dados)
            if invest_total:
                properties["Investimento"] = prop_numero(round(float(invest_total), 2))

            roas_val = None
            if isinstance(dados, dict):
                roas_val = dados.get("roas_medio")
            elif isinstance(dados, list) and dados:
                roas_vals = [float(r.get("roas_medio") or r.get("roas") or 0)
                             for r in dados if r.get("roas_medio") or r.get("roas")]
                roas_val = sum(roas_vals) / len(roas_vals) if roas_vals else None
            if roas_val:
                properties["ROAS"] = prop_numero(round(float(roas_val), 2))

            page = criar_pagina(db_id, properties)
        else:
            page = _criar_pagina_standalone(titulo_pagina)
    else:
        page = _criar_pagina_standalone(titulo_pagina)

    page_id = page["id"]
    url     = page.get("url", f"https://notion.so/{page_id.replace('-', '')}")

    # Adiciona blocos de conteúdo (em lotes de 100 — limite da API)
    LOTE = 95
    for i in range(0, len(blocos), LOTE):
        adicionar_blocos(page_id, blocos[i:i + LOTE])

    logger.info("Relatório publicado no Notion: %s → %s", titulo_pagina, url)
    return page_id, url


def _criar_pagina_standalone(titulo: str) -> dict:
    """
    Cria página standalone quando não há database de destino especificado.
    Usa o database Source OF como destino padrão (todos os dados são Source OF).
    """
    from notion.client import query_database, criar_pagina
    db_info = DATABASES.get("source_of", {})
    pages   = query_database(db_info["data_source_id"], limit=1)
    if pages:
        db_id = pages[0].get("parent", {}).get("database_id")
        if db_id:
            return criar_pagina(db_id, {"Título": prop_titulo(titulo)})
    raise RuntimeError("Não foi possível encontrar o database Source OF no Notion.")



# ── Wrapper para o builder.py ─────────────────────────────────────────────────

def publicar_relatorio_campanha(
    cliente_key: str,
    titulo: str,
    periodo_str: str,
    resumo: dict,
    blocos: list[dict],
) -> dict:
    """
    Wrapper usado pelo builder.py para publicar o relatório semanal.

    Recebe os blocos já montados e publica diretamente no Notion.

    Returns:
        dict com page_id e url
    """
    from notion.client import DATABASES, criar_pagina, adicionar_blocos, query_database, prop_titulo, prop_data, prop_numero
    from datetime import date as _date

    db_info = DATABASES.get(cliente_key, {})
    db_id   = db_info.get("database_id")

    # Resolve database_id dinamicamente se não tiver
    if not db_id and db_info.get("data_source_id"):
        pages = query_database(db_info["data_source_id"], limit=1)
        if pages:
            parent = pages[0].get("parent", {})
            db_id  = parent.get("database_id")
            db_info["database_id"] = db_id

    if not db_id:
        raise ValueError(f"Não foi possível obter database_id para '{cliente_key}'. Verifique a integração Notion.")

    # Extrai data do período para propriedade Data
    partes_periodo = periodo_str.split("→")
    data_inicio_str = None
    data_fim_str    = None
    if len(partes_periodo) == 2:
        def _parse_date(s: str):
            s = s.strip()
            try:
                d, m, a = s.split("/")
                return f"{a}-{m}-{d}"
            except Exception:
                return None
        data_inicio_str = _parse_date(partes_periodo[0])
        data_fim_str    = _parse_date(partes_periodo[1])

    properties = {"Título": prop_titulo(titulo)}
    if data_inicio_str:
        properties["Data"] = prop_data(data_inicio_str, data_fim_str)
    invest = resumo.get("total_invest")
    if invest:
        properties["Investimento"] = prop_numero(round(float(invest), 2))
    roas = resumo.get("roas")
    if roas:
        properties["ROAS"] = prop_numero(round(float(roas), 2))
    compras = resumo.get("total_compras")
    if compras:
        properties["Conversões"] = prop_numero(int(compras))

    page    = criar_pagina(db_id, properties)
    page_id = page["id"]
    url     = page.get("url", f"https://notion.so/{page_id.replace('-', '')}")

    # Publica blocos em lotes de 95 (limite da API Notion)
    LOTE = 95
    for i in range(0, len(blocos), LOTE):
        adicionar_blocos(page_id, blocos[i:i + LOTE])

    logger.info("[Notion] Relatório publicado: %s → %s", titulo, url)
    return {"page_id": page_id, "url": url}
