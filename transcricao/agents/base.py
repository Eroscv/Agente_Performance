"""
transcricao/agents/base.py

Contrato comum dos subagentes do pipeline de Transcrição.
"""
import sqlite3
import sys
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from estatistica import taxa_erro  # noqa: E402


class BaseSubagente(ABC):
    """Cada subagente mapeia 1:1 pra uma Camada do fluxograma documentado
    em "Metodologia aplicada — Subagente de Transcrição" (Notion):

        IngestaoAgent    — Camada 0 (Fundamento)
        ParserAgent      — Camada 1 (Interpretação)
        AnaliseAgent     — Camada 2 (Análise/Claude)
        TicketAgent      — Camada 3 (Regra de negócio: tickets/SLA)
        MetricasAgent    — Camada 3 (Regra de negócio: métricas/IC Wilson)
        ComunicacaoAgent — Camada 4 (Comunicação)

    executar() é o ponto de entrada único de cada subagente — mesmo papel
    que BaseClientAgent.execute() tem em
    agente-performance/agent/clients/__init__.py, só que aqui a
    especialização é por ESTÁGIO do pipeline, não por cliente (este
    subagente não varia por cliente).
    """

    CAMADA: str = ""  # ex: "0 — Ingestão" — preenchido por subclasse

    @abstractmethod
    def executar(self, **kwargs) -> dict:
        """Executa o trabalho desta camada e devolve um resumo (contadores)."""
        raise NotImplementedError

    # ── Métricas por camada (taxa de erro/sucesso com IC de Wilson) ──────
    #
    # ic_wilson() é a técnica já madura do MetricasAgent (Camada 3) —
    # ver "Metodologia aplicada — Subagente de Transcrição" (Notion):
    # eventos de erro são raros, então taxa bruta (%) com n pequeno é
    # enganosa; o IC de Wilson dá o intervalo de confiança real.
    #
    # registrar_evento()/metricas() usam uma tabela compartilhada
    # (log_subagente) — pensada pra camadas que hoje só persistem
    # sucesso (ex: AnaliseAgent, ComunicacaoAgent) e não tinham onde
    # registrar falha de forma consultável historicamente. Camadas que
    # já têm sua própria tabela de log (IngestaoAgent -> sync_log,
    # ParserAgent -> campo vazio em `reunioes`, TicketAgent ->
    # tickets_alerta) sobrescrevem metricas() pra ler da tabela própria
    # em vez desta — ver cada agents/*.py.

    def registrar_evento(self, conn: sqlite3.Connection, fonte_id: str, status: str, mensagem: str = "") -> None:
        """Grava um evento de sucesso/erro desta camada em log_subagente
        (cria a tabela se não existir). status deve ser 'ok' ou 'erro'."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS log_subagente (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                camada      TEXT NOT NULL,
                fonte_id    TEXT,
                status      TEXT NOT NULL,
                mensagem    TEXT,
                criado_em   TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO log_subagente (camada, fonte_id, status, mensagem, criado_em) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.CAMADA, fonte_id, status, mensagem, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    def metricas(self, conn: sqlite3.Connection, janela_dias: int = 90) -> dict:
        """Taxa de erro/sucesso desta camada numa janela, lida de
        log_subagente. Implementação padrão — camadas com tabela própria
        (Ingestao/Parser/Ticket) sobrescrevem este método."""
        desde = (date.today() - timedelta(days=janela_dias)).isoformat()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) as n FROM log_subagente "
                "WHERE camada = ? AND criado_em >= ? GROUP BY status",
                (self.CAMADA, desde),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []  # tabela ainda não existe — nenhum evento registrado ainda
        contagem = {r["status"]: r["n"] for r in rows}
        return taxa_erro(contagem.get("ok", 0), contagem.get("erro", 0))
