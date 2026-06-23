"""
api/app.py

API FastAPI do Agente de Performance Guará.
Expõe endpoints REST para consultas ao banco de campanhas.

Rotas:
  GET  /health                    → status do sistema
  GET  /campanhas                 → lista todas as campanhas
  POST /consulta                  → consulta em linguagem natural
  POST /sql                       → SQL livre (apenas SELECT)
  GET  /resumo                    → resumo geral (com filtros opcionais)
  GET  /top/roas                  → top campanhas por ROAS
  GET  /top/compras               → top campanhas por compras
  GET  /campanha/{nome}           → série temporal de uma campanha
  GET  /campanha/{nome}/funil     → métricas de funil de uma campanha
  GET  /alertas/roas              → campanhas com ROAS baixo
  GET  /evolucao                  → evolução diária geral ou por campanha
  POST /db/reload                 → recarrega o CSV no banco
"""

import logging
import os
from pathlib import Path
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from agent.engine  import consultar, consultar_sql
from agent.queries import (
    listar_campanhas,
    resumo_por_campanha,
    performance_campanha,
    top_campanhas_roas,
    top_campanhas_compras,
    evolucao_diaria,
    alertas_roas_baixo,
    metricas_funil,
    resumo_periodo,
)

app = FastAPI(
    title="Agente de Performance Guará",
    version="2.0.0",
    description="API de consulta a dados de campanhas Meta Ads",
)


# ── Schemas ───────────────────────────────────────────────────────────────────

class ConsultaRequest(BaseModel):
    pergunta: str


class SqlRequest(BaseModel):
    sql: str


# ── Rotas ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    from db.connection import DB_PATH
    return {
        "status": "ok",
        "db_exists": DB_PATH.exists(),
        "db_size_kb": round(DB_PATH.stat().st_size / 1024, 1) if DB_PATH.exists() else 0,
    }


@app.get("/campanhas")
def get_campanhas():
    """Lista todas as campanhas com totais."""
    return listar_campanhas()


@app.post("/consulta")
def post_consulta(req: ConsultaRequest):
    """Consulta em linguagem natural — retorna dados + intenção detectada."""
    try:
        return consultar(req.pergunta)
    except Exception as e:
        logger.exception("Erro em /consulta")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sql")
def post_sql(req: SqlRequest):
    """Executa SQL livre (apenas SELECT)."""
    try:
        return consultar_sql(req.sql)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Erro em /sql")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/resumo")
def get_resumo(
    data_inicio: Optional[str] = Query(None, description="YYYY-MM-DD"),
    data_fim:    Optional[str] = Query(None, description="YYYY-MM-DD"),
):
    """Resumo por campanha com filtros opcionais de data."""
    if data_inicio and data_fim:
        return resumo_periodo(data_inicio, data_fim)
    return resumo_por_campanha(data_inicio, data_fim)


@app.get("/top/roas")
def get_top_roas(
    limit:       int           = Query(10, ge=1, le=50),
    data_inicio: Optional[str] = Query(None),
    data_fim:    Optional[str] = Query(None),
):
    return top_campanhas_roas(limit, data_inicio, data_fim)


@app.get("/top/compras")
def get_top_compras(
    limit:       int           = Query(10, ge=1, le=50),
    data_inicio: Optional[str] = Query(None),
    data_fim:    Optional[str] = Query(None),
):
    return top_campanhas_compras(limit, data_inicio, data_fim)


@app.get("/campanha/{nome}")
def get_campanha(
    nome:        str,
    data_inicio: Optional[str] = Query(None),
    data_fim:    Optional[str] = Query(None),
):
    """Série temporal diária de uma campanha."""
    dados = performance_campanha(nome, data_inicio, data_fim)
    if not dados:
        raise HTTPException(status_code=404, detail=f"Campanha '{nome}' não encontrada")
    return dados


@app.get("/campanha/{nome}/funil")
def get_funil_campanha(
    nome:        str,
    data_inicio: Optional[str] = Query(None),
    data_fim:    Optional[str] = Query(None),
):
    return metricas_funil(nome, data_inicio, data_fim)


@app.get("/alertas/roas")
def get_alertas_roas(threshold: float = Query(1.0, description="ROAS mínimo aceitável")):
    return alertas_roas_baixo(threshold)


@app.get("/evolucao")
def get_evolucao(
    campanha:    Optional[str] = Query(None),
    data_inicio: Optional[str] = Query(None),
    data_fim:    Optional[str] = Query(None),
):
    return evolucao_diaria(campanha, data_inicio, data_fim)


@app.post("/db/reload")
def reload_db():
    """Recarrega o CSV no banco (idempotente)."""
    try:
        from db.loader import load_csv
        n = load_csv()
        return {"status": "ok", "linhas_inseridas": n}
    except Exception as e:
        logger.exception("Erro ao recarregar DB")
        raise HTTPException(status_code=500, detail=str(e))


# ── Entry ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run("api.app:app", host="0.0.0.0", port=port, reload=False)
