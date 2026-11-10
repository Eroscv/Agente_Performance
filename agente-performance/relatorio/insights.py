"""
relatorio/insights.py

Gerador de insights acionáveis via Claude para relatórios diários, semanais e mensais.

Analisa os dados do período e retorna:
  - Lista de ações concretas priorizadas (alta/média/baixa)
  - Diagnóstico de campanhas que precisam de atenção
  - Oportunidades identificadas nos dados

Uso:
    from relatorio.insights import gerar_insights
    blocos = gerar_insights(resumo, campanhas, periodo="diario", cliente_key="source_of")
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_PROMPT_SISTEMA = """Você é um especialista sênior em mídia paga com foco em Meta Ads e Google Ads.
Sua função é analisar dados de campanhas e gerar insights práticos e ações concretas.
Responda sempre em português brasileiro, de forma direta e objetiva.
Nunca invente dados — analise apenas o que foi fornecido."""

_PROMPT_TEMPLATE = """Analise os dados de performance abaixo e gere insights acionáveis.

## Cliente: {cliente_nome}
## Período: {periodo_label} — {periodo_str}
## Plataforma: {plataforma_label}

## Dados do período:
{dados_resumo}

## Campanhas (top por investimento):
{dados_campanhas}

{dados_extras}

---

Gere uma análise estruturada com exatamente estas 3 seções:

### 🔴 AÇÕES URGENTES
Liste até 3 ações que precisam ser tomadas AGORA (problemas críticos: ROAS abaixo do breakeven, alto investimento sem conversão, frequência acima de 4, fadiga criativa).
Formato: "• [campanha/problema] → [ação específica]"
Se não houver urgências, escreva "✅ Nenhuma ação urgente necessária."

### 🟡 OTIMIZAÇÕES RECOMENDADAS
Liste até 4 otimizações para melhorar performance nas próximas 48-72h (ajustes de orçamento, audiências, criativos, lances).
Formato: "• [observação] → [ação específica]"

### 🟢 OPORTUNIDADES
Liste até 3 oportunidades identificadas nos dados (campanhas com bom ROAS para escalar, audiências com baixo CPA, tendências positivas).
Formato: "• [oportunidade] → [como aproveitar]"

Seja específico. Use os nomes reais das campanhas dos dados fornecidos.
Máximo 2 linhas por item. Sem introduções ou conclusões genéricas."""


def _resumo_para_texto(resumo: dict, plataforma: str) -> str:
    """Converte dict de resumo em texto estruturado para o prompt."""
    if not resumo:
        return "Sem dados disponíveis."

    invest = resumo.get("investimento") or resumo.get("total_invest") or 0
    receita = resumo.get("receita") or resumo.get("total_receita") or 0
    roas = resumo.get("roas") or (receita / invest if invest > 0 else None)
    conversoes = resumo.get("conversoes") or resumo.get("compras") or resumo.get("total_compras") or 0
    ctr = resumo.get("ctr")
    cpc = resumo.get("cpc") or resumo.get("cpc_medio")
    cpm = resumo.get("cpm")
    impressoes = resumo.get("impressoes")
    cliques = resumo.get("cliques") or resumo.get("cliques_link")
    campanhas_n = resumo.get("campanhas")
    frequencia = resumo.get("frequencia")

    invest_imp = round(float(invest) * 1.1215, 2) if plataforma == "meta_ads" and invest else None

    linhas = [
        f"- Investimento: R$ {float(invest):,.2f}" + (f" (com imposto: R$ {float(invest_imp):,.2f})" if invest_imp else ""),
        f"- Receita atribuída: R$ {float(receita):,.2f}",
        f"- ROAS: {float(roas):.2f}x" if roas else "- ROAS: sem dados",
        f"- Conversões/compras: {int(float(conversoes))}",
    ]
    if ctr: linhas.append(f"- CTR: {float(ctr):.2f}%")
    if cpc: linhas.append(f"- CPC médio: R$ {float(cpc):.2f}")
    if cpm: linhas.append(f"- CPM: R$ {float(cpm):.2f}")
    if impressoes: linhas.append(f"- Impressões: {int(float(impressoes)):,}")
    if cliques: linhas.append(f"- Cliques: {int(float(cliques)):,}")
    if campanhas_n: linhas.append(f"- Campanhas ativas: {campanhas_n}")
    if frequencia: linhas.append(f"- Frequência média: {float(frequencia):.1f}x")

    return "\n".join(linhas)


def _campanhas_para_texto(campanhas: list[dict], max_camps: int = 10) -> str:
    """Converte lista de campanhas em texto para o prompt."""
    if not campanhas:
        return "Sem dados de campanhas."

    linhas = []
    for c in campanhas[:max_camps]:
        nome = (c.get("campanha") or c.get("nome") or "—")[:60]
        invest = c.get("investimento") or 0
        receita = c.get("receita") or c.get("valor_compras") or c.get("valor_conversao") or 0
        roas = c.get("roas") or c.get("roas_compras")
        if not roas and invest and receita:
            roas = float(receita) / float(invest)
        conv = c.get("conversoes") or c.get("compras") or 0
        freq = c.get("frequencia")
        ctr = c.get("ctr")

        linha = f"- {nome}: R$ {float(invest):,.2f} invest | R$ {float(receita):,.2f} receita | ROAS {float(roas):.2f}x" if roas else f"- {nome}: R$ {float(invest):,.2f} invest | {int(float(conv))} conv"
        if freq: linha += f" | freq {float(freq):.1f}x"
        if ctr: linha += f" | CTR {float(ctr):.2f}%"
        linhas.append(linha)

    return "\n".join(linhas)


def _chamar_claude(prompt: str) -> str:
    """Chama Claude e retorna o texto gerado."""
    try:
        from utils.llm import gerar
        return gerar(_PROMPT_SISTEMA, prompt, max_tokens=1200)
    except Exception as e:
        logger.warning("[Insights] LLM falhou: %s", e)
        return None


def _texto_para_blocos_notion(texto: str) -> list[dict]:
    """Converte o texto de insights em blocos Notion."""
    from notion.blocks import h2, h3, bullet, paragrafo, divider, callout

    if not texto:
        return [
            h2("💡 Insights & Ações"),
            paragrafo("Não foi possível gerar insights automáticos para este período."),
            divider(),
        ]

    blocos = [h2("💡 Insights & Ações Recomendadas"), divider()]

    secao_atual = None
    itens_secao = []

    def _flush_secao():
        nonlocal itens_secao
        for item in itens_secao:
            item = item.strip().lstrip("•").strip()
            if item:
                blocos.append(bullet(item[:1900]))
        itens_secao = []

    for linha in texto.split("\n"):
        linha = linha.strip()
        if not linha:
            continue

        # Detecta cabeçalhos de seção
        if "AÇÕES URGENTES" in linha or "🔴" in linha:
            _flush_secao()
            blocos.append(h3("🔴 Ações Urgentes"))
            secao_atual = "urgente"
        elif "OTIMIZAÇÕES" in linha or "🟡" in linha:
            _flush_secao()
            blocos.append(h3("🟡 Otimizações Recomendadas"))
            secao_atual = "otimizacao"
        elif "OPORTUNIDADES" in linha or "🟢" in linha:
            _flush_secao()
            blocos.append(h3("🟢 Oportunidades"))
            secao_atual = "oportunidade"
        elif linha.startswith("•") or linha.startswith("-"):
            itens_secao.append(linha)
        elif linha.startswith("✅") and secao_atual == "urgente":
            itens_secao.append(linha)
        elif secao_atual and linha and not linha.startswith("#"):
            # Linha de conteúdo sem bullet — adiciona como item
            itens_secao.append(f"• {linha}")

    _flush_secao()
    blocos.append(divider())
    return blocos


def gerar_insights(
    resumo: dict,
    campanhas: list[dict],
    periodo: str = "diario",
    cliente_key: str = "source_of",
    plataforma: str = "meta_ads",
    periodo_str: str = "",
    dados_extras: dict = None,
) -> list[dict]:
    """
    Gera blocos Notion com insights e ações recomendadas via Claude.

    Args:
        resumo:      dict com métricas consolidadas do período
        campanhas:   lista de dicts com dados por campanha
        periodo:     'diario' | 'semanal' | 'mensal'
        cliente_key: 'source_of' | 'pod_kombucha'
        plataforma:  'meta_ads' | 'google_ads'
        periodo_str: string descritiva do período (ex: '14/08/2026')
        dados_extras: dict com dados adicionais (Shopify, comparativo, etc.)

    Returns:
        Lista de blocos Notion
    """
    CLIENTES_NOMES = {"source_of": "Source OF", "pod_kombucha": "POD Kombucha"}
    PLAT_NOMES = {"meta_ads": "Meta Ads", "google_ads": "Google Ads"}
    PERIODO_LABELS = {"diario": "Diário", "semanal": "Semanal", "mensal": "Mensal"}

    cliente_nome   = CLIENTES_NOMES.get(cliente_key, cliente_key)
    plataforma_label = PLAT_NOMES.get(plataforma, plataforma)
    periodo_label  = PERIODO_LABELS.get(periodo, periodo.title())

    resumo_txt    = _resumo_para_texto(resumo, plataforma)
    campanhas_txt = _campanhas_para_texto(campanhas)

    # Dados extras (Shopify, comparativo)
    extras_txt = ""
    if dados_extras:
        partes = []
        shopify = dados_extras.get("shopify")
        if shopify:
            receita_s = shopify.get("receita") or shopify.get("receita_total")
            pedidos = shopify.get("pedidos") or shopify.get("total_pedidos")
            ticket = shopify.get("ticket_medio")
            if receita_s:
                partes.append(f"## Shopify (receita real):\n- Receita: R$ {float(receita_s):,.2f}")
                if pedidos: partes.append(f"- Pedidos: {int(float(pedidos))}")
                if ticket: partes.append(f"- Ticket médio: R$ {float(ticket):,.2f}")
                invest = resumo.get("investimento") or resumo.get("total_invest") or 0
                if invest:
                    roas_real = float(receita_s) / (float(invest) * 1.1215)
                    partes.append(f"- ROAS real (Shopify/invest+imposto): {roas_real:.2f}x")
        comparativo = dados_extras.get("comparativo")
        if comparativo:
            partes.append(f"## Comparativo período anterior:\n{comparativo}")
        if partes:
            extras_txt = "\n".join(partes)

    prompt = _PROMPT_TEMPLATE.format(
        cliente_nome=cliente_nome,
        periodo_label=periodo_label,
        periodo_str=periodo_str or "período atual",
        plataforma_label=plataforma_label,
        dados_resumo=resumo_txt,
        dados_campanhas=campanhas_txt,
        dados_extras=extras_txt,
    )

    logger.info("[Insights] Gerando insights %s para %s·%s", periodo, cliente_key, plataforma)
    texto = _chamar_claude(prompt)

    if not texto:
        from notion.blocks import h2, paragrafo, divider
        return [
            h2("💡 Insights & Ações"),
            paragrafo("⚠️ Não foi possível gerar insights automáticos neste período."),
            divider(),
        ]

    return _texto_para_blocos_notion(texto)
