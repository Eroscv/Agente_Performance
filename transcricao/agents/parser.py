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
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sync_reunioes as _impl  # noqa: E402
from estatistica import taxa_erro  # noqa: E402

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

    def metricas(self, conn, janela_dias: int = 90) -> dict:
        """parsear_reuniao() nunca lança exceção — não há 'erro de
        execução' pra contar. O sinal indireto de falha é um dos campos-
        chave (resumo/decisões/próximas etapas) sair vazio numa reunião
        que foi de fato processada — exatamente o known_gap documentado
        em ../tests/test_parsear_reuniao.py (cabeçalho do Gemini Notes
        não bateu com o literal que o parser procura). Sobrescreve
        BaseSubagente.metricas() — lê de `reunioes`, não de log_subagente."""
        desde = (date.today() - timedelta(days=janela_dias)).isoformat()
        rows = conn.execute(
            "SELECT resumo, decisoes, proximas_etapas FROM reunioes WHERE data >= ?",
            (desde,),
        ).fetchall()
        ok = erro = 0
        for r in rows:
            campos = (r["resumo"], r["decisoes"], r["proximas_etapas"])
            if any(not (c or "").strip() for c in campos):
                erro += 1
            else:
                ok += 1
        return taxa_erro(ok, erro)
