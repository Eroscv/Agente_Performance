"""
agent/hallucination_guard.py — Fact-check pós-geração da resposta do Claude.

`gerar_resposta()` (agent/claude_client.py) já injeta "REGRAS DE GROUNDING"
no prompt, instruindo o Claude a citar só valores presentes nos dados —
mas isso é só instrução *pré*-geração; nada verificava, até aqui, se a
resposta de fato seguiu a regra. Este módulo é essa verificação,
inspirado no HallucinationGuard documentado para o agente Flora
(penalidades por categoria sobre o confidence score), adaptado ao
domínio deste agente (métricas de ads, não e-commerce/pedidos).

Duas checagens implementadas, deliberadamente as de MENOR risco de falso
positivo — ambas comparam a resposta contra fatos categóricos exatos
(cliente, datas), não contra números derivados/calculados:

  cliente_cruzado         — resposta menciona um cliente diferente do
                            solicitado. Este é o risco mais crítico
                            documentado na Metodologia de Melhoria
                            (Notion): "atribuir dado ao cliente certo
                            vem antes até do cálculo estar certo".
  periodo_fora_do_escopo  — resposta cita uma data ISO fora do período
                            pedido (data_inicio/data_fim em meta).

NÃO implementado aqui, de propósito (ver docstring de
`verificar_alucinacao`): fact-check de valores numéricos derivados
(ex: "ROAS subiu 20%") contra os dados brutos — isso teria alto risco
de falso positivo (variações percentuais e somas são cálculos
legítimos do Claude, não invenção) e exigiria um verificador numérico
mais sofisticado do que regex. Documentado como limitação conhecida,
não escondido.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── Sinônimos de cliente, pro regex de detecção ──────────────────────────────
_CLIENTE_KEYWORDS: dict[str, list[str]] = {
    "source_of":    [r"\bsource\s*of\b", r"\bsof\b"],
    "pod_kombucha": [r"\bpod\b", r"\bkombucha\b"],
    "botica":       [r"\bbotica\b"],
}

_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _clientes_mencionados(texto: str) -> set[str]:
    texto_lower = texto.lower()
    encontrados = set()
    for cliente, padroes in _CLIENTE_KEYWORDS.items():
        if any(re.search(p, texto_lower) for p in padroes):
            encontrados.add(cliente)
    return encontrados


def verificar_alucinacao(resposta: str, dados, meta: dict) -> list[str]:
    """
    Verifica a resposta gerada contra os fatos categóricos da consulta
    (cliente, período). Retorna lista de tipos de problema encontrados
    (vazio = nenhum problema).

    Limitação conhecida: não verifica valores numéricos derivados/
    calculados (percentuais, somas) — só fatos categóricos exatos. Ver
    docstring do módulo.
    """
    problemas: list[str] = []
    if not resposta or not isinstance(resposta, str):
        return problemas

    # ── cliente_cruzado ───────────────────────────────────────────────────
    cliente_pedido = meta.get("cliente")
    if cliente_pedido in _CLIENTE_KEYWORDS:
        mencionados = _clientes_mencionados(resposta)
        outros = mencionados - {cliente_pedido}
        if outros:
            problemas.append("cliente_cruzado")
            logger.warning(
                "[hallucination_guard] cliente_cruzado: pedido=%s, mencionados=%s",
                cliente_pedido, sorted(mencionados),
            )

    # ── periodo_fora_do_escopo ────────────────────────────────────────────
    data_inicio = meta.get("data_inicio")
    data_fim    = meta.get("data_fim")
    if data_inicio and data_fim:
        datas_citadas = set(_ISO_DATE_RE.findall(resposta))
        fora = {d for d in datas_citadas if not (data_inicio <= d <= data_fim)}
        if fora:
            problemas.append("periodo_fora_do_escopo")
            logger.warning(
                "[hallucination_guard] periodo_fora_do_escopo: pedido=%s→%s, citadas=%s",
                data_inicio, data_fim, sorted(fora),
            )

    return problemas
