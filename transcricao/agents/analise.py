"""
transcricao/agents/analise.py

AnaliseAgent — Camada 2 (Análise) do pipeline de Transcrição.

Delega para analisador_reunioes.py — não reimplementa a chamada ao
Claude nem o envio ao Slack, só organiza o fluxo que hoje vive em
analisador_reunioes.main().
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analisador_reunioes as _impl  # noqa: E402

from .base import BaseSubagente  # noqa: E402

logger = logging.getLogger(__name__)


class AnaliseAgent(BaseSubagente):
    """Camada 2 — Análise (Claude): sentimento, alertas, síntese por
    reunião."""

    CAMADA = "2 — Análise"

    def pendentes(self, conn, todos: bool = False) -> list:
        return _impl.reunioes_pendentes(conn, todos)

    def analisar(self, reuniao) -> dict:
        return _impl.analisar_com_claude(reuniao)

    def persistir(self, conn, reuniao, resultado: dict) -> bool:
        _impl.salvar_analise_sqlite(conn, reuniao, resultado)
        return _impl.salvar_analise_bq(reuniao, resultado)

    def notificar(self, reuniao, resultado: dict, dry_run: bool = False) -> None:
        _impl.enviar_slack(reuniao, resultado, dry_run=dry_run)

    def executar(self, todos: bool = False, dry_run: bool = False) -> dict:
        """Replica analisador_reunioes.main(): pega pendentes, analisa
        com Claude, persiste (SQLite+BQ) e notifica no Slack — salvo em
        dry_run."""
        conn = _impl.get_conn()
        pendentes = self.pendentes(conn, todos)
        logger.info("Reuniões para analisar: %d", len(pendentes))

        analisadas = alertas_total = erros = 0
        for reuniao in pendentes:
            titulo = reuniao["titulo"]
            fonte_id = reuniao["fonte_id"]
            try:
                resultado = self.analisar(reuniao)
                n_alertas = len(resultado.get("alertas", []))

                if not dry_run:
                    self.persistir(conn, reuniao, resultado)
                self.notificar(reuniao, resultado, dry_run=dry_run)

                analisadas += 1
                alertas_total += n_alertas
                if not dry_run:
                    self.registrar_evento(conn, fonte_id, "ok")
            except json.JSONDecodeError as e:
                logger.error("  ❌ JSON inválido do Claude em '%s': %s", titulo, e)
                erros += 1
                if not dry_run:
                    self.registrar_evento(conn, fonte_id, "erro", f"JSON inválido: {e}")
            except Exception as e:
                logger.error("  ❌ Erro ao analisar '%s': %s", titulo, e)
                erros += 1
                if not dry_run:
                    self.registrar_evento(conn, fonte_id, "erro", str(e))

        conn.close()
        resumo = {"analisadas": analisadas, "alertas": alertas_total, "erros": erros}
        logger.info(
            "Análise concluída — analisadas: %d | alertas: %d | erros: %d",
            analisadas, alertas_total, erros,
        )
        return resumo
