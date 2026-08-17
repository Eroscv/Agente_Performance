"""
api/relatorio_history.py

Endpoint REST para consulta ao histórico de relatórios gerados.

Rotas:
    GET /relatorios           → lista últimos N relatórios (com filtros opcionais)
    GET /relatorios/ultimo    → último relatório de um cliente/periodicidade
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/relatorios", tags=["Relatórios"])


@router.get("")
def get_relatorios(
    cliente_key:   Optional[str] = Query(None, description="Chave do cliente (ex: source_of)"),
    periodicidade: Optional[str] = Query(None, description="Periodicidade: diario, semanal, mensal, periodo"),
    limit:         int           = Query(50, ge=1, le=500, description="Máximo de registros"),
):
    """
    Retorna os últimos N relatórios registrados no banco.

    Filtros opcionais:
    - **cliente_key**: filtra por cliente (source_of, pod_kombucha, ...)
    - **periodicidade**: filtra por frequência (diario, semanal, mensal, periodo)
    - **limit**: número máximo de resultados (padrão: 50, máximo: 500)
    """
    try:
        from db.relatorio_log import listar_relatorios
        rows = listar_relatorios(
            cliente_key=cliente_key,
            periodicidade=periodicidade,
            limit=limit,
        )
        return {"total": len(rows), "relatorios": rows}
    except Exception as e:
        logger.exception("Erro em GET /relatorios")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ultimo")
def get_ultimo_relatorio(
    cliente_key:   str = Query(..., description="Chave do cliente (ex: source_of)"),
    periodicidade: str = Query(..., description="Periodicidade: diario, semanal, mensal, periodo"),
):
    """
    Retorna o último relatório registrado para um cliente + periodicidade.

    Útil para saber quando foi gerado o último relatório e qual foi seu status.
    """
    try:
        from db.relatorio_log import ultimo_relatorio
        row = ultimo_relatorio(cliente_key=cliente_key, periodicidade=periodicidade)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Nenhum relatório encontrado para {cliente_key}/{periodicidade}",
            )
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erro em GET /relatorios/ultimo")
        raise HTTPException(status_code=500, detail=str(e))
