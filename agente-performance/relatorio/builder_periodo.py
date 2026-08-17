"""
relatorio/builder_periodo.py

Gerador de relatório para períodos customizados.
Alias genérico que delega para gerar_relatorio_semanal.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from relatorio.builder_semanal import gerar_relatorio_semanal


def gerar_relatorio_periodo(
    cliente: str = "source_of",
    data_inicio: date = None,
    data_fim: date = None,
    publicar: bool = True,
) -> dict:
    """Alias genérico para períodos customizados."""
    return gerar_relatorio_semanal(
        cliente=cliente,
        data_inicio=data_inicio,
        data_fim=data_fim,
        publicar=publicar,
    )
