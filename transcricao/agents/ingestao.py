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
        self._slack_client = None  # injetado via set_slack_client() para notificações

    def set_slack_client(self, client) -> None:
        """Injeta o cliente Slack para notificações automáticas de falha."""
        self._slack_client = client

    def listar_novos(self, service, conn, todos: bool = False) -> list[dict]:
        """Lista arquivos do Drive ainda não processados (ou todos, se
        todos=True)."""
        arquivos = _impl.listar_docx(service)
        if todos:
            return arquivos
        return [a for a in arquivos if not _impl.ja_processado(conn, a["id"])]

    def processar_um(self, service, conn, arquivo: dict, refill: bool = False) -> dict:
        """Baixa, parseia (ParserAgent) e grava uma reunião.
        Se `refill=True`, apenas preenche campos vazios (sem criar duplicata).
        Levanta exceção em caso de erro — quem chama decide como logar/contar."""
        file_id = arquivo["id"]
        nome    = arquivo["name"]

        conteudo = _impl.baixar_docx(service, file_id, arquivo.get("mimeType", _impl.MIME_DOCX))
        dados    = self._parser.executar(conteudo, nome)

        if refill:
            atualizado = _impl.atualizar_reuniao_campos_vazios(conn, dados, nome, file_id)
            if atualizado:
                _impl.salvar_log(conn, file_id, nome, "ok", f"Refill: {dados['titulo']}")
            return {"dados": dados, "atualizado": atualizado, "mongo_ok": False, "bq_ok": False}

        _impl.salvar_reuniao(conn, dados, nome, file_id)

        mongo_ok = _impl.salvar_mongo(dados, nome, file_id)
        bq_ok    = _impl.salvar_bigquery(dados, nome, file_id)

        _impl.salvar_log(conn, file_id, nome, "ok", f"Salvo: {dados['titulo']}")
        return {"dados": dados, "mongo_ok": mongo_ok, "bq_ok": bq_ok}

    def executar(self, todos: bool = False, refill: bool = False) -> dict:
        """Replica o fluxo de sync_reunioes.main(): lista, filtra por
        dedup (a menos que todos=True), processa cada arquivo novo.

        Se `refill=True`, reprocessa reuniões já existentes que tenham
        campos-chave (resumo/decisões/próximas etapas) vazios —
        útil após melhorias no parser (ex: suporte a Granola).
        """
        service = _impl.get_drive_service()
        conn    = _impl.get_conn()

        arquivos = _impl.listar_docx(service)
        logger.info("Arquivos .docx encontrados no Drive: %d", len(arquivos))

        novos = pulados = erros = atualizados = 0
        for arq in arquivos:
            file_id, nome = arq["id"], arq["name"]

            ja_existe = _impl.ja_processado(conn, file_id)

            if refill and ja_existe:
                # Verifica se algum campo chave está vazio
                row = conn.execute(
                    "SELECT resumo, decisoes, proximas_etapas FROM reunioes WHERE fonte_id = ?",
                    (file_id,)
                ).fetchone()
                campos_vazios = row and any(
                    not (row[c] or "").strip()
                    for c in ("resumo", "decisoes", "proximas_etapas")
                )
                if campos_vazios:
                    logger.info("  🔄 Refill (campos vazios): %s", nome)
                    try:
                        res = self.processar_um(service, conn, arq, refill=True)
                        if res.get("atualizado"):
                            atualizados += 1
                        else:
                            logger.info("  ⏭  Ainda sem conteúdo após refill: %s", nome)
                    except Exception as e:
                        logger.error("  ❌ Erro refill em %s: %s", nome, e)
                        erros += 1
                else:
                    pulados += 1
                continue

            if not todos and ja_existe:
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
                # Notifica Slack — compatível com contexto sync e async
                try:
                    import asyncio as _aio
                    from slack_status_atas import notificar_falha_slack as _nfs
                    _slack = getattr(self, "_slack_client", None)
                    if _slack:
                        try:
                            # Tenta usar loop existente (contexto async do Slack Bolt)
                            loop = _aio.get_running_loop()
                            loop.create_task(_nfs(_slack, nome, str(e)))
                        except RuntimeError:
                            # Fora de contexto async (cron/CLI): cria loop temporário
                            try:
                                _aio.run(_nfs(_slack, nome, str(e)))
                            except Exception as _re:
                                logger.debug("[notif-ata] asyncio.run falhou: %s", _re)
                    else:
                        logger.info("[notif-ata] Sem slack_client — configure via set_slack_client()")
                except Exception as _ne:
                    logger.debug("[notif-ata] Não foi possível notificar: %s", _ne)

        conn.close()
        if _impl._mongo_client:
            _impl._mongo_client.close()
        if _impl._BQ_AVAILABLE and _impl._bq_client is not None:
            try:
                _impl._bq_client.close()
            except Exception:
                pass

        resumo = {"novos": novos, "pulados": pulados, "erros": erros, "atualizados": atualizados}
        logger.info(
            "Sync concluído — novos: %d | pulados: %d | atualizados: %d | erros: %d",
            novos, pulados, atualizados, erros,
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
