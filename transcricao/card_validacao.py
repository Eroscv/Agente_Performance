"""
transcricao/card_validacao.py

Validação do título e área de um card antes de criar no Notion.
Evita que o Claude classifique errado a frente/área de uma tarefa.

Uso:
    ok, erros = validar_card(tarefa, area, reuniao_titulo)
    if not ok:
        # log + notifica + pula criação
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Regras de consistência ─────────────────────────────────────────────────────

# Se o título contém estas palavras → área esperada
_REGRAS_TITULO_AREA = [
    # palavras no título  →  área esperada
    (re.compile(r"\b(roas|cpc|ctr|budget|verba|campanha|meta ads|google ads|tráfego|mídia paga)", re.I), "Performance"),
    (re.compile(r"\b(arte|criativo|criativos|copy|peça|banner|video|vídeo|roteiro)", re.I),             "3P"),
    (re.compile(r"\b(gestão|processo|pipeline|reunião|squad|okr|kpi|metas)", re.I),                     "Gestão"),
    (re.compile(r"\b(landing page|lp |email|disparo|comunicação|newsletter)", re.I),                    "3P"),
]

# Responsáveis válidos (aceita prefixo fuzzy)
_RESPONSAVEIS_CONHECIDOS = {
    "bianca", "daniel", "guilherme", "ana", "lucas", "eros",
    "gabriel", "mateus", "camila", "fernanda", "pedro", "lara",
}

# Comprimento máximo razoável de título
_MAX_TITULO = 200
_MIN_TITULO = 5


def validar_card(
    tarefa: dict,
    area: str,
    reuniao_titulo: str = "",
) -> tuple[bool, list[str]]:
    """
    Valida título e área de um card.

    Returns:
        (ok, lista_de_avisos)
        ok=True → pode criar normalmente
        ok=False → deve logar + (opcionalmente) notificar + pular
    """
    erros: list[str] = []
    avisos: list[str] = []

    titulo = (tarefa.get("titulo") or "").strip()
    resp   = (tarefa.get("responsavel_nome") or "").strip().lower()

    # ── Título vazio ou muito curto ────────────────────────────────────────────
    if not titulo or len(titulo) < _MIN_TITULO:
        erros.append(f"Título muito curto ou vazio: '{titulo}'")

    # ── Título parece genérico / placeholder ──────────────────────────────────
    genericos = re.compile(
        r"^(tarefa\s*\d*|task\s*\d*|a fazer|to.?do|item\s*\d*|pendência\s*\d*)$", re.I
    )
    if genericos.match(titulo):
        erros.append(f"Título genérico/placeholder: '{titulo}'")

    # ── Título muito longo (Claude às vezes gera frases completas) ─────────────
    if len(titulo) > _MAX_TITULO:
        erros.append(f"Título longo demais ({len(titulo)} chars): '{titulo[:60]}…'")

    # ── Consistência título × área ─────────────────────────────────────────────
    areas_sugeridas = set()
    for regex, area_esperada in _REGRAS_TITULO_AREA:
        if regex.search(titulo):
            areas_sugeridas.add(area_esperada)

    if areas_sugeridas and area not in areas_sugeridas:
        avisos.append(
            f"Área possivelmente errada: título sugere {areas_sugeridas} mas área é '{area}'"
        )
        # Aviso apenas — não bloqueia, mas loga

    # ── Responsável não reconhecido ────────────────────────────────────────────
    if resp and not any(resp.startswith(c) for c in _RESPONSAVEIS_CONHECIDOS):
        avisos.append(f"Responsável não reconhecido: '{tarefa.get('responsavel_nome')}'")

    # Loga avisos mas não bloqueia
    for av in avisos:
        logger.warning("[card-validacao] ⚠️  %s | card='%s' | reuniao='%s'", av, titulo[:60], reuniao_titulo)

    ok = len(erros) == 0
    if not ok:
        for er in erros:
            logger.error("[card-validacao] ❌ %s | card='%s'", er, titulo[:60])

    return ok, erros + avisos


def sugerir_area(titulo: str) -> Optional[str]:
    """Retorna a área sugerida pelo título, ou None se ambíguo."""
    sugestoes = set()
    for regex, area_esperada in _REGRAS_TITULO_AREA:
        if regex.search(titulo):
            sugestoes.add(area_esperada)
    return next(iter(sugestoes)) if len(sugestoes) == 1 else None
