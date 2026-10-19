"""
transcricao/compliance_guard.py — Checagens de qualidade/esquema do JSON
devolvido por analisar_com_claude() (Camada 2). Adaptado do
ComplianceGuard em agente-performance/agent/compliance_guard.py — mas
aqui a saída é JSON estruturado (não texto livre pro usuário), então as
regras mudam de forma: em vez de "resposta vazia pro usuário", é
"esquema incompleto"; em vez de "URL externa", é "vazamento do próprio
prompt interno nos campos de texto".

Regras implementadas:
  schema_invalido      — faltam chaves obrigatórias do contrato
                         definido em PROMPT_SISTEMA (analisador_reunioes.py)
  sentimento_invalido   — "sentimento" fora de positivo/neutro/negativo
  blank_response        — resumo_analise vazio
  prompt_leak            — resumo_analise ou alguma descrição de alerta
                         ecoa fragmentos do próprio PROMPT_SISTEMA
  wrong_language         — resumo_analise não está em português
                         (heurística de stopwords — mesma limitação
                         documentada na versão do Agente de Performance)
"""
from __future__ import annotations

import re

_CHAVES_OBRIGATORIAS = ("sentimento", "alertas", "clientes_mencionados", "resumo_analise")
_SENTIMENTOS_VALIDOS = {"positivo", "neutro", "negativo"}

_STOPWORDS_PT = {"de", "para", "não", "que", "com", "em", "os", "as", "do", "da", "é", "foi"}
_STOPWORDS_EN = {"the", "and", "is", "to", "of", "in", "for", "with", "was", "are"}

# Fragmentos que só deveriam existir dentro do PROMPT_SISTEMA, nunca
# ecoados nos campos de texto que o Claude devolve.
_PROMPT_MARKERS = (
    "Responda APENAS com JSON",
    "analista de reuniões de uma agência",
    "Só inclua alertas reais",
)


def verificar_compliance(resultado: dict) -> list[str]:
    """
    Verifica o JSON devolvido por analisar_com_claude() contra regras de
    qualidade/esquema. Retorna lista de regras violadas (vazio = nenhuma).
    """
    problemas: list[str] = []

    if not isinstance(resultado, dict):
        problemas.append("schema_invalido")
        return problemas

    faltantes = [k for k in _CHAVES_OBRIGATORIAS if k not in resultado]
    if faltantes:
        problemas.append("schema_invalido")

    sentimento = resultado.get("sentimento")
    if sentimento is not None and sentimento not in _SENTIMENTOS_VALIDOS:
        problemas.append("sentimento_invalido")

    resumo = (resultado.get("resumo_analise") or "").strip()
    if not resumo:
        problemas.append("blank_response")
    else:
        palavras = set(re.findall(r"[a-zà-ú]+", resumo.lower()))
        pt_hits = len(palavras & _STOPWORDS_PT)
        en_hits = len(palavras & _STOPWORDS_EN)
        if en_hits > pt_hits and en_hits >= 2:
            problemas.append("wrong_language")

    textos_para_checar = [resumo] + [
        (a.get("descricao") or "") for a in (resultado.get("alertas") or []) if isinstance(a, dict)
    ]
    if any(marcador.lower() in t.lower() for t in textos_para_checar for marcador in _PROMPT_MARKERS):
        problemas.append("prompt_leak")

    return problemas
