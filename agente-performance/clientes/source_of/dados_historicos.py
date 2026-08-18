"""
clientes/source_of/dados_historicos.py

Re-exporta dados históricos hardcoded da Source OF que vivem em
relatorio/shared/blocos.py. Centraliza a referência para que
builder_shared.py e outros módulos possam importar de um único lugar.
"""
from relatorio.shared.blocos import (
    _DADOS_HISTORICO,
    _META_Q3,
    _RECOMPRAS_HISTORICO,
    _RECOMPRAS_ESTADOS,
)

__all__ = [
    "_DADOS_HISTORICO",
    "_META_Q3",
    "_RECOMPRAS_HISTORICO",
    "_RECOMPRAS_ESTADOS",
]
