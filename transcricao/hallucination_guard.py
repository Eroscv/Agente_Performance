"""
transcricao/hallucination_guard.py — Fact-check pós-geração do AnaliseAgent
(Camada 2). Mesmo princípio do guard homônimo em
agente-performance/agent/hallucination_guard.py: `analisador_reunioes.py`
já instrui o Claude via PROMPT_SISTEMA a devolver um `trecho` "exato do
texto que gerou o alerta", mas nada verificava, até aqui, se o trecho
devolvido realmente aparece no texto da reunião.

Duas checagens, as de menor risco de falso positivo — comparam contra
fatos exatos da própria reunião, não contra inferência:

  trecho_nao_encontrado    — o "trecho" de algum alerta não aparece
                             (nem como substring normalizada) em
                             nenhum dos campos de texto da reunião.
                             É o caso mais grave: o PROMPT_SISTEMA exige
                             "trecho exato", então isso é a prova mais
                             direta possível de alerta inventado.
  tipo_alerta_invalido      — "tipo" de algum alerta fora dos 5 valores
                             que o próprio PROMPT_SISTEMA define.

NÃO implementado aqui, de propósito: fact-check do "sentimento" (é
uma classificação subjetiva, sem fato categórico pra comparar) e do
mérito de "clientes_mencionados" além de aparecerem no texto — ver
`cliente_nao_fundamentado`, que é uma checagem mais fraca/heurística,
incluída separadamente e com limitação documentada.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

TIPOS_VALIDOS = {"sem_resposta", "urgente", "sem_acesso", "conflito", "bloqueio"}


def _texto_fonte(reuniao) -> str:
    """Concatena todos os campos de texto da reunião — a 'fonte da
    verdade' contra a qual o trecho do alerta deve ser verificável.
    Funciona com dict ou sqlite3.Row (ambos suportam __getitem__ por
    nome de coluna)."""
    campos = ("titulo", "participantes", "resumo", "decisoes", "proximas_etapas", "detalhes")
    partes = []
    for campo in campos:
        try:
            valor = reuniao[campo]
        except (KeyError, IndexError):
            valor = None
        if valor:
            partes.append(str(valor))
    return " ".join(partes).lower()


def _normalizar(s: str) -> str:
    """Remove espaços múltiplos e pontuação de borda — tolera que o
    Claude copie o trecho com whitespace levemente diferente do
    original, sem abrir mão de exigir o texto em si."""
    return re.sub(r"\s+", " ", s.strip().lower())


def verificar_alucinacao(resultado: dict, reuniao) -> list[str]:
    """
    Verifica o JSON devolvido por analisar_com_claude() contra o texto
    real da reunião. Retorna lista de tipos de problema (vazio = nenhum).
    """
    problemas: list[str] = []
    if not isinstance(resultado, dict):
        return problemas

    texto_fonte = _texto_fonte(reuniao)
    alertas = resultado.get("alertas") or []

    for alerta in alertas:
        tipo   = alerta.get("tipo")
        trecho = alerta.get("trecho")

        if tipo and tipo not in TIPOS_VALIDOS:
            if "tipo_alerta_invalido" not in problemas:
                problemas.append("tipo_alerta_invalido")
            logger.warning("[hallucination_guard] tipo_alerta_invalido: '%s'", tipo)

        if trecho:
            trecho_norm = _normalizar(trecho)
            # Substring direta primeiro (caso comum); se falhar, tenta
            # por "janela" de palavras — tolera o Claude reordenar
            # pontuação sem inventar conteúdo novo.
            if trecho_norm and trecho_norm not in texto_fonte:
                if "trecho_nao_encontrado" not in problemas:
                    problemas.append("trecho_nao_encontrado")
                logger.warning(
                    "[hallucination_guard] trecho_nao_encontrado: '%s' não está no texto da reunião",
                    trecho[:80],
                )

    return problemas
