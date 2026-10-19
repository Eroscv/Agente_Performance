"""
transcricao/agents/parser.py

ParserAgent — Camada 1 (Interpretação) do pipeline de Transcrição.

Já tem cobertura de teste própria em ../tests/test_parsear_reuniao.py
(2 known_gap documentados via xfail — ver lá antes de corrigir algo
chamado a partir daqui).

Delega integralmente para sync_reunioes.extrair_texto() e
parsear_reuniao() — zero lógica nova neste arquivo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sync_reunioes as _impl  # noqa: E402

from .base import BaseSubagente  # noqa: E402


class ParserAgent(BaseSubagente):
    """Camada 1 — Interpretação: texto bruto do .docx -> estrutura
    (data, hora, título, participantes, resumo, decisões, próximas
    etapas, detalhes)."""

    CAMADA = "1 — Interpretação"

    def extrair_texto(self, docx_bytes: bytes) -> str:
        return _impl.extrair_texto(docx_bytes)

    def parsear(self, texto: str, nome_arquivo: str) -> dict:
        return _impl.parsear_reuniao(texto, nome_arquivo)

    def executar(self, docx_bytes: bytes, nome_arquivo: str) -> dict:
        """Conveniência: extrai texto do .docx e parseia em um passo."""
        texto = self.extrair_texto(docx_bytes)
        return self.parsear(texto, nome_arquivo)
