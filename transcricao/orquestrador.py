#!/usr/bin/env python3
"""
transcricao/orquestrador.py

Orquestrador real do pipeline de Transcrição — substitui run_sync.sh,
que só encadeava 4 dos 5 scripts via bash, sem tratamento de erro entre
etapas e sem rodar a Camada 4 (ComunicacaoAgent/criar_tarefas_notion.py
nunca aparecia lá).

Diferente do OrchestratorAgent do Agente de Performance (que roteia por
requisição, em tempo real, decidindo qual ClientAgent chamar), este
orquestrador roda o pipeline inteiro em lote, uma vez por invocação —
é o motor por trás do cron, não um despachante de perguntas.

Encadeamento (ordem de dependência de DADOS, não de import):
  IngestaoAgent    (Camada 0+1) -> grava `reunioes`
  AnaliseAgent     (Camada 2)   -> lê `reunioes`, grava `analise_reunioes`
  TicketAgent      (Camada 3a)  -> lê `analise_reunioes`, grava `tickets_alerta`
  MetricasAgent    (Camada 3b)  -> lê `tickets_alerta` (independente, sempre roda)
  ComunicacaoAgent (Camada 4)   -> lê `reunioes.proximas_etapas` (independente
                                     de Análise/Ticket — por isso não pode
                                     "sumir" do pipeline como sumia em run_sync.sh)

Cada etapa roda isolada (try/except): uma falha numa camada é logada e
registrada no resultado, mas NÃO interrompe as etapas seguintes — mesma
filosofia de "falha de um cliente não derruba os outros" já usada em
agente-performance/scripts/cron_todos_relatorios.py. É uma melhoria
sobre run_sync.sh (que já tinha esse comportamento de fato, por não
usar `set -e`), só que agora com registro explícito de que algo falhou,
em vez de só aparecer no log e ninguém notar.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents import (  # noqa: E402
    AnaliseAgent,
    ComunicacaoAgent,
    IngestaoAgent,
    MetricasAgent,
    TicketAgent,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class OrquestradorTranscricao:
    """Roda o pipeline de Transcrição inteiro, etapa por etapa, isolando
    falha de cada camada das demais.

    Os subagentes são injetáveis (parâmetros opcionais do __init__) —
    mesmo padrão usado em IngestaoAgent(parser=...) — pra permitir teste
    com fakes, sem precisar mockar Drive/Claude/Notion/Slack/BigQuery.
    """

    def __init__(
        self,
        ingestao: IngestaoAgent | None = None,
        analise: AnaliseAgent | None = None,
        ticket: TicketAgent | None = None,
        metricas_agent: MetricasAgent | None = None,
        comunicacao: ComunicacaoAgent | None = None,
    ):
        self.ingestao = ingestao or IngestaoAgent()
        self.analise = analise or AnaliseAgent()
        self.ticket = ticket or TicketAgent()
        self.metricas_agent = metricas_agent or MetricasAgent()
        self.comunicacao = comunicacao or ComunicacaoAgent()

    def _rodar_etapa(self, nome_camada: str, fn, *args, **kwargs) -> dict:
        logger.info("▶ %s", nome_camada)
        try:
            resumo = fn(*args, **kwargs)
            logger.info("✅ %s concluída: %s", nome_camada, resumo)
            return {"status": "ok", "resumo": resumo}
        except Exception as e:
            logger.error("❌ %s falhou: %s", nome_camada, e, exc_info=True)
            return {"status": "erro", "erro": str(e)}

    def executar(
        self,
        todos: bool = False,
        dry_run: bool = False,
        pular_comunicacao: bool = False,
        janela_metricas_dias: int = 90,
    ) -> dict:
        """Roda as 5 etapas em sequência. Retorna um dict por etapa:
        {"status": "ok"|"erro", "resumo": ...} ou {"status": "erro", "erro": ...}.
        Nenhuma etapa é pulada por falha de outra — só pular_comunicacao=True
        pula a Camada 4 de propósito (ex: rodar sem credenciais do Notion)."""
        resultado: dict[str, dict] = {}

        resultado["ingestao"] = self._rodar_etapa(
            "Camada 0+1 — Ingestão", self.ingestao.executar, todos=todos
        )
        resultado["analise"] = self._rodar_etapa(
            "Camada 2 — Análise", self.analise.executar, todos=todos, dry_run=dry_run
        )
        resultado["ticket"] = self._rodar_etapa(
            "Camada 3a — Tickets", self.ticket.executar, dry_run=dry_run
        )
        resultado["metricas"] = self._rodar_etapa(
            "Camada 3b — Métricas SLA",
            self.metricas_agent.executar,
            janela_dias=janela_metricas_dias,
            bq=not dry_run,
        )
        if not pular_comunicacao:
            resultado["comunicacao"] = self._rodar_etapa(
                "Camada 4 — Comunicação (Notion)",
                self.comunicacao.executar,
                todos=todos,
                dry_run=dry_run,
            )

        falhas = [camada for camada, r in resultado.items() if r["status"] == "erro"]
        if falhas:
            logger.warning("Pipeline concluído com falha em: %s", ", ".join(falhas))
        else:
            logger.info("Pipeline concluído sem falhas.")

        return resultado


def main():
    parser = argparse.ArgumentParser(description="Orquestrador do pipeline de Transcrição")
    parser.add_argument("--todos", action="store_true", help="Reprocessa/reanalisa tudo (não só pendentes)")
    parser.add_argument("--dry-run", action="store_true", help="Não salva/envia nada de verdade")
    parser.add_argument("--pular-comunicacao", action="store_true", help="Não roda a Camada 4 (cards Notion)")
    parser.add_argument("--janela-metricas", type=int, default=90, help="Janela em dias p/ métricas SLA (padrão: 90)")
    args = parser.parse_args()

    orquestrador = OrquestradorTranscricao()
    resultado = orquestrador.executar(
        todos=args.todos,
        dry_run=args.dry_run,
        pular_comunicacao=args.pular_comunicacao,
        janela_metricas_dias=args.janela_metricas,
    )

    falhou = any(r["status"] == "erro" for r in resultado.values())
    sys.exit(1 if falhou else 0)


if __name__ == "__main__":
    main()
