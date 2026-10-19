"""
transcricao/agents/comunicacao.py

ComunicacaoAgent — Camada 4 (Comunicação) do pipeline de Transcrição.
Delega para criar_tarefas_notion.py.

Achado durante esta estruturação (não corrigido aqui, fora de escopo):
criar_tarefas_notion.tarefas_ja_criadas() existe mas nunca é chamada em
nenhum lugar — a função de dedup está morta, então rodar com --todos
sobre uma janela já processada pode criar cards duplicados no Notion.
"""
import logging
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import criar_tarefas_notion as _impl  # noqa: E402

from .base import BaseSubagente  # noqa: E402

logger = logging.getLogger(__name__)


class ComunicacaoAgent(BaseSubagente):
    """Camada 4 — Comunicação: tarefas extraídas da reunião -> cards no
    Notion (paralelo ao alerta de Slack da Camada 2 / AnaliseAgent)."""

    CAMADA = "4 — Comunicação"

    def extrair_tarefas(self, proximas_etapas: str, titulo: str) -> list[dict]:
        return _impl.parsear_tarefas(proximas_etapas, titulo)

    def detectar_contexto(self, texto_combinado: str, titulo: str) -> tuple[list[str], str]:
        return _impl.detectar_clientes(texto_combinado), _impl.detectar_area(titulo)

    def criar_card(self, **kwargs) -> bool:
        return _impl.criar_card_notion(**kwargs)

    def _buscar_reunioes(self, todos: bool) -> list:
        conn = sqlite3.connect(_impl.DB_PATH)
        conn.row_factory = sqlite3.Row
        if todos:
            reunioes = conn.execute("""
                SELECT id, data, titulo, participantes, proximas_etapas, fonte_id
                FROM reunioes
                WHERE proximas_etapas IS NOT NULL AND proximas_etapas != ''
                ORDER BY data DESC
            """).fetchall()
        else:
            desde = (date.today() - timedelta(days=30)).isoformat()
            reunioes = conn.execute("""
                SELECT id, data, titulo, participantes, proximas_etapas, fonte_id
                FROM reunioes
                WHERE proximas_etapas IS NOT NULL AND proximas_etapas != ''
                  AND data >= ?
                ORDER BY data DESC
            """, (desde,)).fetchall()
        conn.close()
        return reunioes

    def executar(self, todos: bool = False, dry_run: bool = False) -> dict:
        """Replica criar_tarefas_notion.main(): busca reuniões com
        próximas etapas, extrai tarefas, detecta cliente/área e cria um
        card no Notion por tarefa."""
        reunioes = self._buscar_reunioes(todos)
        logger.info("Reuniões com próximas etapas: %d", len(reunioes))

        criados = pulados = erros = 0
        for reuniao in reunioes:
            titulo   = reuniao["titulo"]
            data_r   = reuniao["data"]
            fonte_id = reuniao["fonte_id"]

            texto_combinado = (
                (titulo or "") + " " +
                (reuniao["participantes"] or "") + " " +
                (reuniao["proximas_etapas"] or "")
            )
            clientes, area = self.detectar_contexto(texto_combinado, titulo)

            tarefas = self.extrair_tarefas(reuniao["proximas_etapas"] or "", titulo)
            if not tarefas:
                logger.info("  ⏭  Sem tarefas parseáveis: %s (%s)", titulo, data_r)
                pulados += 1
                continue

            logger.info(
                "  📋 %s (%s) → %d tarefas | área=%s | clientes=%s",
                titulo, data_r, len(tarefas), area, clientes,
            )

            for tarefa in tarefas:
                ok = self.criar_card(
                    tarefa=tarefa,
                    reuniao_data=data_r,
                    reuniao_titulo=titulo,
                    fonte_id=fonte_id,
                    clientes=clientes,
                    area=area,
                    dry_run=dry_run,
                )
                if ok:
                    logger.info("    ✅ %s → %s", tarefa["responsavel_nome"], tarefa["titulo"])
                    criados += 1
                else:
                    logger.warning("    ❌ %s → %s", tarefa["responsavel_nome"], tarefa["titulo"])
                    erros += 1

        resumo = {"criados": criados, "pulados": pulados, "erros": erros}
        logger.info(
            "\nConcluído — criados: %d | pulados: %d | erros: %d",
            criados, pulados, erros,
        )
        return resumo
