"""
main.py

Entrypoint principal do Agente de Performance Guará.
FastAPI + Slack Bolt (AsyncApp) via adaptador HTTP.

Rotas Slack:
  POST /slack/events     → eventos Bolt (menções, DMs)

Rotas API:
  GET  /health
  GET  /campanhas
  POST /consulta
  POST /sql
  GET  /resumo
  GET  /top/roas
  GET  /top/compras
  GET  /campanha/{nome}
  GET  /campanha/{nome}/funil
  GET  /alertas/roas
  GET  /evolucao
  POST /db/reload

Inicialização:
  python main.py
"""

import asyncio
import logging
import os

import uvicorn
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("⚙️  Agente de Performance Guará iniciando...")

    # Verifica banco legado (fallback)
    from db.connections import LEGACY_DB_PATH as DB_PATH
    if not DB_PATH.exists():
        logger.warning("Banco legado não encontrado: %s", DB_PATH)
    else:
        logger.info("Banco legado: %s", DB_PATH)

    # Inicializa conexão federada (multi-DB)
    from db.connections import get_federated_conn, inspect_federated_views, PERFORMANCE_DBS, INBOX_DB, OUTBOX_DB, AGENT_DB
    try:
        fed = get_federated_conn()
        info = inspect_federated_views()
        logger.info(
            "🗄️  DB federado OK — views: %s | rows: %s | attached: %s",
            info["views"],
            {k: v for k, v in info["row_counts"].items()},
            info["attached_dbs"],
        )
        for (ck, plat), path in PERFORMANCE_DBS.items():
            if path.exists():
                logger.info("  ✅ %s/%s → %s", ck, plat, path.name)
            else:
                logger.warning("  ⚠️  %s/%s → AUSENTE: %s", ck, plat, path)
        for label, path in [("inbox", INBOX_DB), ("outbox", OUTBOX_DB), ("agent", AGENT_DB)]:
            status = "✅" if path.exists() else "⚠️ "
            logger.info("  %s logs/%s → %s", status, label, path.name)
    except Exception as e:
        logger.error("Erro ao inicializar DB federado: %s", e)

    logger.info("✅ Pronto para receber consultas")

    # ── Dashboard ingest — desabilitado: gerenciado pelo cron dashboard-ingest-15min ──
    # (removido loop interno para evitar duplicação com o cron externo)

    yield

    logger.info("⚙️  Agente encerrado.")


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Agente de Performance Guará",
    version="2.0.0",
    description="Consultas inteligentes a dados de campanhas Meta Ads via Slack + REST",
    lifespan=lifespan,
)

# ── Slack ─────────────────────────────────────────────────────────────────────
from slack.handlers import get_slack_app
slack_app     = get_slack_app()
slack_handler = AsyncSlackRequestHandler(slack_app)


@app.post("/slack/events")
async def slack_events(request: Request):
    return await slack_handler.handle(request)


# ── Monta rotas da API ────────────────────────────────────────────────────────
from api.app import app as api_router
app.mount("/", api_router)


# ── Entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    logger.info("Iniciando na porta %d", port)
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
