"""
transcricao/agents/ticket.py

TicketAgent — Camada 3 (Regra de negócio: tickets/SLA) do pipeline de
Transcrição. Delega para gestor_alertas.py.
"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gestor_alertas as _impl  # noqa: E402

from .base import BaseSubagente  # noqa: E402

logger = logging.getLogger(__name__)


class TicketAgent(BaseSubagente):
    """Camada 3 — Regra de negócio: abre tickets a partir de alertas
    críticos e verifica resolução (via Claude) em reuniões posteriores."""

    CAMADA = "3 — Regra de negócio (tickets)"

    def abrir_tickets_pendentes(self, conn, dry_run: bool = False) -> int:
        analises = conn.execute("""
            SELECT a.fonte_id, a.titulo, a.data, a.alertas
            FROM analise_reunioes a
            WHERE a.alertas != '[]' AND a.alertas IS NOT NULL
            ORDER BY a.data
        """).fetchall()

        total = 0
        for analise in analises:
            n = _impl.abrir_tickets_da_analise(conn, analise, dry_run)
            if n:
                logger.info(
                    "  📂 %s (%s) → %d ticket(s) aberto(s)",
                    analise["titulo"], analise["data"], n,
                )
                total += n
        return total

    def processar_resolucoes(self, conn, dry_run: bool = False) -> int:
        return _impl.processar_resolucoes(conn, dry_run)

    def status(self, conn) -> None:
        _impl.exibir_status(conn)

    def fechar_manual(self, conn, ticket_id: str) -> bool:
        agora = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            UPDATE tickets_alerta
            SET status='fechado', resolvido_em=?, resolvido_por='manual', atualizado_em=?
            WHERE ticket_id=?
        """, (agora, agora, ticket_id))
        conn.commit()
        t = conn.execute(
            "SELECT * FROM tickets_alerta WHERE ticket_id=?", (ticket_id,)
        ).fetchone()
        if t:
            _impl.upsert_ticket_bq(t)
            return True
        return False

    def executar(self, dry_run: bool = False) -> dict:
        """Replica gestor_alertas.main() --processar: abre tickets novos
        + verifica resoluções."""
        conn = _impl.get_conn()
        total_abertos = self.abrir_tickets_pendentes(conn, dry_run)
        logger.info("Tickets abertos: %d", total_abertos)

        logger.info("Verificando resoluções com Claude...")
        total_resolvidos = self.processar_resolucoes(conn, dry_run)
        logger.info("Tickets resolvidos: %d", total_resolvidos)

        conn.close()
        return {"abertos": total_abertos, "resolvidos": total_resolvidos}
