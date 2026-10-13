"""
relatorio/shared/constants.py

Constantes compartilhadas entre os builders de relatório.
"""
from __future__ import annotations

import os
import signal as _signal_mod
import functools as _functools_mod
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
DB_PATH  = BASE_DIR / "data" / "campanhas.db"

# Timeout em segundos para queries SQLite no builder
_SQLITE_TIMEOUT = int(os.getenv("BUILDER_SQLITE_TIMEOUT", "30"))

# Clientes mapeados para chave Notion
CLIENTES = {
    "source_of":          "Source OF",
    "consorcio_sorteado": "Consórcio Sorteado",
    "maximo_servico":     "Máximo Serviço",
    "be_mushin":          "BE MUSHIN",
    "bazar_precatorio":   "Bazar do Precatório",
}

_MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março",    4: "Abril",
    5: "Maio",    6: "Junho",     7: "Julho",    8: "Agosto",
    9: "Setembro",10: "Outubro",  11: "Novembro", 12: "Dezembro",
}

_MESES_PT_ABR = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}


def _com_timeout(segundos: int = 5):
    """Decorator que limita tempo de execução (usa SIGALRM — apenas Linux)."""
    def decorator(fn):
        @_functools_mod.wraps(fn)
        def wrapper(*args, **kwargs):
            def _handler(s, f):
                raise TimeoutError(f"{fn.__name__} timeout ({segundos}s)")
            old = _signal_mod.signal(_signal_mod.SIGALRM, _handler)
            _signal_mod.alarm(segundos)
            try:
                return fn(*args, **kwargs)
            except TimeoutError:
                return None
            finally:
                _signal_mod.alarm(0)
                _signal_mod.signal(_signal_mod.SIGALRM, old)
        return wrapper
    return decorator
