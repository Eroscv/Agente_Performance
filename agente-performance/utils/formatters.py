"""
utils/formatters.py

Formatadores compartilhados por todos os módulos do projeto.
Import: from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n, roas_emoji
"""


def fmt_brl(v) -> str:
    if v is None: return "—"
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


def fmt_x(v) -> str:
    if v is None: return "—"
    try:
        return f"{float(v):.2f}x"
    except Exception:
        return "—"


def fmt_pct(v) -> str:
    if v is None: return "—"
    try:
        return f"{float(v):.2f}%"
    except Exception:
        return "—"


def fmt_n(v, dec: int = 0) -> str:
    if v is None: return "—"
    try:
        return f"{float(v):,.{dec}f}".replace(",", ".")
    except Exception:
        return "—"


def roas_emoji(roas) -> str:
    if roas is None: return "⚪"
    r = float(roas)
    if r >= 4:  return "🟢"
    if r >= 2:  return "🟡"
    return "🔴"


def delta_str(atual, anterior, maior_melhor: bool = True) -> str:
    """Variação percentual com seta e emoji. Ex: ✅ ↑ 12,3%"""
    try:
        a, b = float(atual), float(anterior)
        if b == 0: return ""
        pct = (a - b) / abs(b) * 100
        if abs(pct) < 0.5: return "→ estável"
        up   = pct > 0
        icon = "✅" if (up == maior_melhor) else "⚠️"
        seta = "↑" if up else "↓"
        return f"{icon} {seta} {abs(pct):.1f}%"
    except Exception:
        return ""
