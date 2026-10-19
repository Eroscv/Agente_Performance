"""
agent/compliance_guard.py — Checagens de qualidade/compliance da resposta
final, pós-geração. Inspirado no ComplianceGuard documentado para o
agente Flora (lista de regras com penalidade sobre o confidence score),
adaptado ao contexto deste agente (resposta de Slack/API sobre métricas
de ads, não chat de atendimento ao cliente).

Regras implementadas:
  blank_response    — resposta vazia ou só espaços/pontuação
  short_response     — resposta claramente curta demais pra uma pergunta
                        que tinha dados reais pra analisar
  wrong_language     — resposta não está em português (heurística de
                        stopwords — ver limitação na docstring)
  external_url       — resposta contém URL que não é de domínio
                        esperado (ex: Slack, Notion, o próprio produto)
  prompt_leak         — resposta contém fragmentos reconhecíveis das
                        instruções internas do system prompt (ex: as
                        marcações "REGRAS DE GROUNDING", "LINHA ÂNCORA
                        OBRIGATÓRIA" que só deveriam existir no prompt,
                        nunca na resposta ao usuário)
"""
from __future__ import annotations

import re

_MIN_LEN_RESPOSTA = 20  # chars — abaixo disso, com dados reais, é suspeito

_STOPWORDS_PT = {"de", "para", "não", "que", "com", "em", "os", "as", "do", "da", "é", "foi"}
_STOPWORDS_EN = {"the", "and", "is", "to", "of", "in", "for", "with", "was", "are"}

# Domínios esperados numa resposta deste agente (Slack, Notion — onde ele
# publica relatórios — e nenhum outro).
_DOMINIOS_PERMITIDOS = ("slack.com", "notion.so", "notion.site")
_URL_RE = re.compile(r"https?://([a-zA-Z0-9.\-]+)")

# Marcadores que só deveriam aparecer dentro do prompt interno, nunca na
# resposta final — se aparecerem, o modelo "ecoou" a instrução em vez de
# só segui-la.
_PROMPT_MARKERS = (
    "REGRAS DE GROUNDING",
    "LINHA ÂNCORA OBRIGATÓRIA",
    "[CONFIANÇA DA RESPOSTA",
    "[CONFIANÇA DO CONTEXTO",
)


def verificar_compliance(resposta: str) -> list[str]:
    """
    Verifica a resposta final contra regras de qualidade/compliance.
    Retorna lista de regras violadas (vazio = nenhuma violação).

    Limitação conhecida (wrong_language): heurística de contagem de
    stopwords comuns — funciona bem pra detectar resposta inteira em
    inglês, mas não detecta code-switching parcial ou erros sutis de
    tradução. Não é um detector de idioma robusto.
    """
    problemas: list[str] = []

    if resposta is None or not resposta.strip():
        problemas.append("blank_response")
        return problemas  # resposta vazia — nenhuma outra regra se aplica

    texto = resposta.strip()

    if len(texto) < _MIN_LEN_RESPOSTA:
        problemas.append("short_response")

    palavras = set(re.findall(r"[a-zà-ú]+", texto.lower()))
    pt_hits = len(palavras & _STOPWORDS_PT)
    en_hits = len(palavras & _STOPWORDS_EN)
    if en_hits > pt_hits and en_hits >= 2:
        problemas.append("wrong_language")

    for match in _URL_RE.finditer(texto):
        dominio = match.group(1).lower()
        if not any(dominio == d or dominio.endswith("." + d) for d in _DOMINIOS_PERMITIDOS):
            problemas.append("external_url")
            break

    if any(marcador in texto for marcador in _PROMPT_MARKERS):
        problemas.append("prompt_leak")

    return problemas
