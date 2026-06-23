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

    # Garante que o banco existe
    from db.connection import DB_PATH
    if not DB_PATH.exists():
        logger.info("Banco não encontrado — executando loader...")
        from db.loader import load_csv
        n = load_csv()
        logger.info("Banco criado com %d registros", n)
    else:
        logger.info("Banco encontrado: %s", DB_PATH)

    logger.info("✅ Pronto para receber consultas")
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
