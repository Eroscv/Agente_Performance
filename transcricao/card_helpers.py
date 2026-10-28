"""
card_helpers.py
Funções auxiliares: parse de prazo, match de usuário/cliente/frente.
"""
from datetime import datetime, timedelta
from typing import Optional
from card_constants import USUARIOS_UNICOS, CLIENTES_LISTA, FRENTES_LISTA


def parse_prazo(texto: str) -> Optional[str]:
    """Converte texto livre para ISO date."""
    t = texto.strip().lower()
    hoje = datetime.now().date()
    if t in ("amanhã", "amanha"):
        return (hoje + timedelta(days=1)).isoformat()
    if t == "hoje":
        return hoje.isoformat()
    if t.startswith("+"):
        try:
            return (hoje + timedelta(days=int(t[1:]))).isoformat()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m"):
        try:
            parsed = datetime.strptime(texto.strip(), fmt).date()
            # Formato sem ano: usa ano corrente (ou próximo se data já passou)
            if fmt == "%d/%m":
                parsed = parsed.replace(year=hoje.year)
                if parsed < hoje:
                    parsed = parsed.replace(year=hoje.year + 1)
            return parsed.isoformat()
        except ValueError:
            continue
    return None


def match_usuario(texto: str) -> Optional[str]:
    """Resolve nome digitado (número ou nome parcial) para nome exato."""
    t = texto.strip()
    if not t:
        return None
    try:
        idx = int(t) - 1
        if 0 <= idx < len(USUARIOS_UNICOS):
            return list(USUARIOS_UNICOS.keys())[idx]
    except ValueError:
        pass
    t_lower = t.lower()
    for nome in USUARIOS_UNICOS:
        if t_lower in nome.lower():
            return nome
    return None


def match_cliente(texto: str) -> Optional[str]:
    """Resolve cliente digitado (número ou nome parcial)."""
    t = texto.strip()
    try:
        idx = int(t) - 1
        if 0 <= idx < len(CLIENTES_LISTA):
            return CLIENTES_LISTA[idx]
    except ValueError:
        pass
    t_lower = t.lower()
    for c in CLIENTES_LISTA:
        if t_lower in c.lower():
            return c
    return None


def match_frente(texto: str) -> Optional[str]:
    """Resolve frente digitada (número ou nome parcial)."""
    t = texto.strip()
    try:
        idx = int(t) - 1
        if 0 <= idx < len(FRENTES_LISTA):
            return FRENTES_LISTA[idx]
    except ValueError:
        pass
    t_lower = t.lower()
    for f in FRENTES_LISTA:
        if t_lower in f.lower():
            return f
    return None
