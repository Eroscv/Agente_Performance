"""
transcricao/agents/metricas.py

MetricasAgent — Camada 3 (Regra de negócio: métricas/SLA) do pipeline
de Transcrição. Delega para metricas_alertas.py — inclui ic_wilson(),
citado na Metodologia aplicada (Notion) como a parte mais madura do
sistema inteiro (Intervalo de Confiança de Wilson em vez de taxa bruta,
porque alertas urgentes são eventos raros).
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import metricas_alertas as _impl  # noqa: E402

from .base import BaseSubagente  # noqa: E402


class MetricasAgent(BaseSubagente):
    """Camada 3 — Regra de negócio: métricas de SLA com Intervalo de
    Confiança de Wilson (eventos raros, não taxa bruta)."""

    CAMADA = "3 — Regra de negócio (métricas/SLA)"

    def ic_wilson(self, k: int, n: int, z: float = 1.96) -> tuple[float, float]:
        return _impl.ic_wilson(k, n, z)

    def coletar(self, conn, janela_dias: int = 90) -> dict:
        return _impl.coletar_metricas(conn, janela_dias=janela_dias)

    def formatar(self, metricas: dict) -> str:
        return _impl.formatar_relatorio(metricas)

    def publicar_bq(self, metricas: dict) -> None:
        _impl.salvar_metricas_bq(metricas)

    def publicar_slack(self, relatorio_texto: str) -> None:
        _impl.enviar_slack(relatorio_texto)

    def executar(self, janela_dias: int = 90, slack: bool = False, bq: bool = False) -> dict:
        """Coleta métricas e, opcionalmente, publica (Slack/BigQuery).
        Formatação/impressão em stdout fica a cargo de quem chama (é
        detalhe de CLI, não de regra de negócio) — ver metricas_alertas.main()."""
        conn = sqlite3.connect(_impl.DB_PATH)
        conn.row_factory = sqlite3.Row
        m = self.coletar(conn, janela_dias=janela_dias)
        conn.close()

        if slack:
            self.publicar_slack(self.formatar(m))
        if bq:
            self.publicar_bq(m)
        return m
