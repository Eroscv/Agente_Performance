"""
transcricao/agents/ingestao.py

IngestaoAgent — Camada 0 (Fundamento) do pipeline de Transcrição.

Detecção no Drive, download, dedup (fonte_id) e gravação em
SQLite/MongoDB/BigQuery. Delega para as funções já existentes em
sync_reunioes.py — este módulo não reimplementa nenhuma lógica, só
organiza o fluxo que hoje vive dentro de sync_reunioes.main() numa
classe reutilizável e composível.

Camada 1 (parsear_reuniao) é delegada ao ParserAgent — mesma separação
documentada na Metodologia aplicada — Subagente de Transcrição (Notion),
ainda que as duas camadas convivam no mesmo arquivo legado
(sync_reunioes.py) hoje.
"""
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sync_reunioes as _impl  # noqa: E402
from estatistica import taxa_erro  # noqa: E402

from .base import BaseSubagente  # noqa: E402
from .parser import ParserAgent  # noqa: E402

logger = logging.getLogger(__name__)


class IngestaoAgent(BaseSubagente):
    """Camada 0 — Fundamento: Drive -> download -> parse (via ParserAgent)
    -> dedup -> gravação tripla (SQLite/Mongo/BigQuery)."""

    CAMADA = "0 — Ingestão"

    def __init__(self, parser: ParserAgent | None = None):
        self._parser = parser or ParserAgent()

    def listar_novos(self, service, conn, todos: bool = False) -> list[dict]:
        """Lista arquivos do Drive ainda não processados (ou todos, se
        todos=True)."""
        arquivos = _impl.listar_docx(service)
        if todos:
            return arquivos
        return [a for a in arquivos if not _impl.ja_processado(conn, a["id"])]

    def processar_um(self, service, conn, arquivo: dict) -> dict:
        """Baixa, parseia (ParserAgent) e grava uma reunião. Levanta
        exceção em caso de erro — quem chama decide como logar/contar
        (ver executar())."""
        file_id = arquivo["id"]
        nome    = arquivo["name"]

        conteudo = _impl.baixar_docx(service, file_id, arquivo.get("mimeType", _impl.MIME_DOCX))
        dados    = self._parser.executar(conteudo, nome)
        _impl.salvar_reuniao(conn, dados, nome, file_id)

        mongo_ok = _impl.salvar_mongo(dados, nome, file_id)
        bq_ok    = _impl.salvar_bigquery(dados, nome, file_id)

        _impl.salvar_log(conn, file_id, nome, "ok", f"Salvo: {dados['titulo']}")
        return {"dados": dados, "mongo_ok": mongo_ok, "bq_ok": bq_ok}

    def executar(self, todos: bool = False) -> dict:
        """Replica o fluxo de sync_reunioes.main(): lista, filtra por
        dedup (a menos que todos=True), processa cada arquivo novo e
        fecha as conexões ao final."""
        service = _impl.get_drive_service()
        conn    = _impl.get_conn()

        arquivos = _impl.listar_docx(service)
        logger.info("Arquivos .docx encontrados no Drive: %d", len(arquivos))

        novos = pulados = erros = 0
        for arq in arquivos:
            file_id, nome = arq["id"], arq["name"]
            if not todos and _impl.ja_processado(conn, file_id):
                logger.info("  ⏭  Já processado: %s", nome)
                pulados += 1
                continue
            logger.info("  📥 Processando: %s", nome)
            try:
                self.processar_um(service, conn, arq)
                novos += 1
            except Exception as e:
                logger.error("  ❌ Erro em %s: %s", nome, e)
                _impl.salvar_log(conn, file_id, nome, "erro", str(e))
                erros += 1

        conn.close()
        if _impl._mongo_client:
            _impl._mongo_client.close()
        if _impl._BQ_AVAILABLE and _impl._bq_client is not None:
            try:
                _impl._bq_client.close()
            except Exception:
                pass

        resumo = {"novos": novos, "pulados": pulados, "erros": erros}
        logger.info(
            "Sync concluído — novos: %d | pulados: %d | erros: %d",
            novos, pulados, erros,
        )
        return resumo

    def metricas(self, conn, janela_dias: int = 90) -> dict:
        """Taxa de erro/sucesso lida de sync_log — tabela que já existia
        antes desta sessão, criada por get_conn() em sync_reunioes.py.
        Sobrescreve BaseSubagente.metricas() (que leria de log_subagente,
        tabela nova e vazia pra esta camada)."""
        desde = (date.today() - timedelta(days=janela_dias)).isoformat()
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM sync_log WHERE criado_em >= ? GROUP BY status",
            (desde,),
        ).fetchall()
        contagem = {r["status"]: r["n"] for r in rows}
        return taxa_erro(contagem.get("ok", 0), contagem.get("erro", 0))
