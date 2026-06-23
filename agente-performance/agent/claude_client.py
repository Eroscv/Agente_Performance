"""
agent/claude_client.py

Cliente Anthropic para o Agente de Performance Guará.
Recebe dados estruturados das queries e gera resposta conversacional em PT-BR.
"""

import logging
import os
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None

SYSTEM_PROMPT = """Você é o Agente de Performance Guará, especialista sênior em mídia paga com foco em Meta Ads (Facebook/Instagram).

Você responde perguntas sobre dados de campanhas de performance com base nos dados reais fornecidos.

## Sua missão
- Analisar dados de campanhas e entregar insights acionáveis
- Falar em português brasileiro, de forma direta e objetiva
- Usar emojis com moderação para tornar a leitura mais agradável no Slack
- Formatar respostas para Slack (use *negrito*, _itálico_, `código`, listas com •)
- Nunca inventar dados — só analise o que foi fornecido
- Quando identificar algo preocupante (ROAS baixo, custo alto), sinalizar claramente
- Quando identificar algo positivo, destacar

## Métricas principais
- *ROAS*: quanto cada R$1 investido retornou em receita (> 3 = bom, > 5 = excelente, < 1 = prejuízo)
- *CPC*: custo por clique no link (quanto menor, melhor)
- *CTR*: taxa de cliques (quanto maior, melhor engajamento)
- *CPM*: custo por 1.000 impressões
- *Frequência*: vezes que a mesma pessoa viu o anúncio (> 4 pode causar fadiga)
- *Custo por compra*: investimento / número de compras

## Formato das respostas
- Seja conciso mas completo
- Destaque o número mais importante primeiro
- Use tabelas simples quando comparar múltiplas campanhas
- Sempre termine com uma recomendação ou próximo passo quando relevante
"""


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def gerar_resposta(
    pergunta:    str,
    dados:       Any,
    intent:      str,
    meta:        dict,
    dados_extra: dict = None,
    contexto:    str  = "",
) -> str:
    """
    Gera resposta conversacional com Claude a partir dos dados das queries.

    Args:
        pergunta:    Pergunta original do usuário
        dados:       Resultado da query principal (list[dict] ou dict)
        intent:      Intenção principal detectada
        meta:        Metadados da consulta (datas, campanha, etc.)
        dados_extra: Resultados de intenções adicionais (múltiplas intenções)
        contexto:    Histórico da thread para perguntas de acompanhamento

    Returns:
        Texto formatado para Slack
    """
    client = get_client()

    # Monta contexto dos dados
    dados_str   = _formatar_dados(dados, intent)
    periodo_str = _formatar_periodo(meta)

    # Dados extras de múltiplas intenções
    extras_str = ""
    if dados_extra:
        partes = []
        for intent_extra, dados_e in dados_extra.items():
            partes.append(f"\n[Dados adicionais — {intent_extra}]:\n{_formatar_dados(dados_e, intent_extra)}")
        extras_str = "".join(partes)

    # Contexto de thread anterior
    contexto_str = f"\n{contexto}\n" if contexto else ""

    user_message = f"""Pergunta do usuário: {pergunta}
{contexto_str}
Intenção detectada: {intent}
{periodo_str}

Dados retornados do banco:
{dados_str}{extras_str}

Responda a pergunta do usuário com base nesses dados. Seja analítico e direto."""

    try:
        model = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    except anthropic.APIError as e:
        logger.error("Erro na API Anthropic: %s", e)
        return f"⚠️ Erro ao gerar análise: {e}"


def _formatar_dados(dados: Any, intent: str) -> str:
    """Serializa dados para texto estruturado legível pelo Claude."""
    if dados is None:
        return "Nenhum dado encontrado."

    if isinstance(dados, dict):
        if not dados:
            return "Nenhum dado encontrado."
        linhas = []
        for k, v in dados.items():
            if v is not None:
                linhas.append(f"  {k}: {v}")
        return "\n".join(linhas)

    if isinstance(dados, list):
        if not dados:
            return "Nenhum resultado encontrado para os filtros aplicados."

        # Limita a 50 linhas para não estourar o contexto
        amostra = dados[:50]
        linhas = []
        for i, row in enumerate(amostra, 1):
            campos = ", ".join(f"{k}={v}" for k, v in row.items() if v is not None)
            linhas.append(f"  {i}. {campos}")

        resultado = "\n".join(linhas)
        if len(dados) > 50:
            resultado += f"\n  ... (e mais {len(dados) - 50} registros)"
        return resultado

    return str(dados)


def _formatar_periodo(meta: dict) -> str:
    inicio = meta.get("data_inicio")
    fim    = meta.get("data_fim")
    if inicio and fim:
        return f"Período filtrado: {inicio} a {fim}"
    if inicio:
        return f"A partir de: {inicio}"
    if fim:
        return f"Até: {fim}"
    return "Período: todos os dados disponíveis"
