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

# ── Router: histórico de relatórios ──────────────────────────────────────────
from api.relatorio_history import router as relatorios_router
app.include_router(relatorios_router)


# ── Schemas ───────────────────────────────────────────────────────────────────

class ConsultaRequest(BaseModel):
    pergunta: str


class SqlRequest(BaseModel):
    sql: str


# ── Rotas ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    from db.connections import LEGACY_DB_PATH as DB_PATH
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
    import sqlite3 as _sqlite3
    try:
        return consultar_sql(req.sql)
    except ValueError as e:
        # SELECT não permitido / SQL bloqueado
        raise HTTPException(status_code=400, detail=str(e))
    except _sqlite3.OperationalError as e:
        # Erro de sintaxe SQL ou tabela inexistente
        raise HTTPException(
            status_code=400,
            detail=f"Erro de SQL: {e}. Verifique a sintaxe e os nomes das colunas.",
        )
    except _sqlite3.DatabaseError as e:
        # Outros erros do banco (corrupção, lock, etc)
        raise HTTPException(
            status_code=500,
            detail=f"Erro no banco de dados: {e}",
        )
    except Exception as e:
        logger.exception("Erro inesperado em /sql")
        raise HTTPException(status_code=500, detail=f"Erro inesperado: {e}")


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
        from db.legacy.loader_csv import load_csv
        from agent.cache import clear as cache_clear
        n = load_csv()
        cache_clear()
        return {"status": "ok", "linhas_inseridas": n, "cache_invalidado": True}
    except Exception as e:
        logger.exception("Erro ao recarregar DB")
        raise HTTPException(status_code=500, detail=str(e))


# ── Notion ──────────────────────────────────────────────────────────────────────

@app.get("/notion/status")
def notion_status():
    """Verifica conexão com o Notion e retorna info do workspace."""
    try:
        from notion.client import testar_conexao, listar_databases_disponiveis
        bot = testar_conexao()
        databases = listar_databases_disponiveis()
        return {
            "status":         "ok",
            "bot_name":       bot["name"],
            "workspace":      bot["bot"]["workspace_name"],
            "databases":      databases,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/notion/databases")
def notion_databases():
    """Lista todos os databases configurados."""
    from notion.client import listar_databases_disponiveis
    return listar_databases_disponiveis()


@app.get("/notion/relatorios/{cliente}")
def notion_relatorios(
    cliente:     str,
    limit:       int          = Query(20, description="Número de registros"),
    data_inicio: Optional[str] = Query(None, description="Data inicial ISO (2026-06-01)"),
    data_fim:    Optional[str] = Query(None, description="Data final ISO (2026-06-30)"),
):
    """
    Retorna relatórios de um cliente do Notion.
    
    Clientes disponíveis: consorcio_sorteado, source_of, maximo_servico, be_mushin, bazar_precatorio
    """
    try:
        from notion.client import get_relatorios_cliente
        return get_relatorios_cliente(cliente, limit=limit, data_inicio=data_inicio, data_fim=data_fim)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Erro ao buscar relatórios Notion")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/notion/publicar")
def notion_publicar(body: dict):
    """
    Publica um relatório no Notion.
    
    Body: {
        "cliente_key": "consorcio_sorteado",
        "titulo": "Consórcio Sorteado · meta · Ontem · 23/06/2026",
        "plataforma": "meta",
        "data": "2026-06-23",
        "investimento": 1234.56,
        "roas": 3.5,
        "conversoes": 42,
        "cliente_nome": "Consórcio Sorteado"
    }
    """
    try:
        from notion.client import publicar_relatorio
        page = publicar_relatorio(
            cliente_key  = body["cliente_key"],
            titulo       = body["titulo"],
            plataforma   = body["plataforma"],
            data         = body["data"],
            investimento = float(body["investimento"]),
            roas         = float(body["roas"]) if body.get("roas") is not None else None,
            conversoes   = int(body.get("conversoes", 0)),
            cliente_nome = body.get("cliente_nome"),
        )
        return {"status": "ok", "page_id": page["id"], "url": page.get("url")}
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Campo obrigatório ausente: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Erro ao publicar relatório no Notion")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/notion/buscar")
def notion_buscar(body: dict = None):
    """Busca páginas e databases no workspace Notion."""
    try:
        from notion.client import buscar
        query = (body or {}).get("query", "")
        tipo  = (body or {}).get("tipo")
        limit = (body or {}).get("limit", 20)
        results = buscar(query=query, tipo=tipo, limit=limit)
        from notion.client import pagina_para_dict
        return [
            {"id": r["id"], "object": r["object"], **pagina_para_dict(r)}
            for r in results
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Entry ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run("api.app:app", host="0.0.0.0", port=port, reload=False)


# ── Endpoints Base de Conhecimento (Notion KB) ────────────────────────────────

@app.get("/kb/entradas")
def get_kb_entradas(
    apenas_aprovadas: bool = Query(False, description="Filtrar apenas aprovadas"),
    limit: int = Query(20, description="Máximo de entradas"),
):
    """Lista entradas da Base de Conhecimento no Notion."""
    try:
        from notion.knowledge_base import listar_entradas
        return {"entradas": listar_entradas(apenas_aprovadas=apenas_aprovadas, limit=limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/kb/aprovar/{page_id}")
def post_kb_aprovar(page_id: str, aprovado_por: str = Query("", description="Nome de quem aprovou")):
    """Aprova manualmente uma entrada da Base de Conhecimento."""
    try:
        from notion.knowledge_base import aprovar_entrada
        ok = aprovar_entrada(page_id, aprovado_por=aprovado_por)
        return {"ok": ok, "page_id": page_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/kb/contexto")
def get_kb_contexto(
    intent: Optional[str] = Query(None, description="Intent para filtrar"),
    campanha: Optional[str] = Query(None, description="Campanha para filtrar"),
    limit: int = Query(5, description="Número máximo de entradas"),
):
    """Retorna o contexto KB que seria injetado no prompt do Claude."""
    try:
        from notion.knowledge_base import buscar_contexto, formatar_contexto_para_prompt
        entradas  = buscar_contexto(intent=intent, campanha=campanha, limit=limit)
        formatado = formatar_contexto_para_prompt(entradas)
        return {"entradas": entradas, "prompt_preview": formatado}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Shopify — sincronização em tempo real ─────────────────────────────────────

class ShopifySyncRequest(BaseModel):
    dias:       int  = 7
    data_inicio: Optional[str] = None  # YYYY-MM-DD
    data_fim:    Optional[str] = None  # YYYY-MM-DD
    dry_run:    bool = False
    full:       bool = False


@app.get("/shopify/status")
def get_shopify_status():
    """
    Status da sincronização Shopify.
    Retorna data mínima/máxima sincronizada, receita total e última atualização.
    """
    try:
        from agent.queries import status_shopify_sync
        return status_shopify_sync()
    except Exception as e:
        logger.exception("Erro em /shopify/status")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/shopify/sync")
def post_shopify_sync(req: ShopifySyncRequest):
    """
    Sincroniza receita Shopify → SQLite para o período especificado.

    Parâmetros:
        dias:        Últimos N dias (padrão: 7). Ignorado se data_inicio/data_fim informados.
        data_inicio: Data inicial YYYY-MM-DD (opcional).
        data_fim:    Data final YYYY-MM-DD (opcional).
        dry_run:     Se true, apenas simula — não grava no banco.
        full:        Se true, sincroniza desde jan/2023 (reprocessamento completo).
    """
    from datetime import date, timedelta
    try:
        from db.shopify_sync import sincronizar
        if req.full:
            d_inicio = date(2023, 1, 1)
            d_fim    = date.today()
        elif req.data_inicio and req.data_fim:
            d_inicio = date.fromisoformat(req.data_inicio)
            d_fim    = date.fromisoformat(req.data_fim)
        else:
            d_fim    = date.today()
            d_inicio = d_fim - timedelta(days=req.dias - 1)

        resultado = sincronizar(d_inicio, d_fim, dry_run=req.dry_run)
        return resultado
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Erro em /shopify/sync")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/shopify/receita")
def get_shopify_receita(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim:    str = Query(..., description="YYYY-MM-DD"),
):
    """
    Retorna métricas Shopify para o período (dados sincronizados).
    Inclui ROAS real (Receita Shopify / Investimento Meta ajustado).
    """
    try:
        from agent.queries import receita_shopify_periodo
        return receita_shopify_periodo(data_inicio, data_fim)
    except Exception as e:
        logger.exception("Erro em /shopify/receita")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/shopify/diario")
def get_shopify_diario(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim:    str = Query(..., description="YYYY-MM-DD"),
):
    """
    Receita Shopify por dia no período (para gráficos de evolução).
    """
    try:
        from agent.queries import metricas_shopify_diario
        return metricas_shopify_diario(data_inicio, data_fim)
    except Exception as e:
        logger.exception("Erro em /shopify/diario")
        raise HTTPException(status_code=500, detail=str(e))
