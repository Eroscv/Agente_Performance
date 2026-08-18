"""
agent/clients/relatorio/base.py

BaseRelatorioAgent — contrato que todos os subagentes de relatório implementam.

Responsabilidade:
  - Receber tipo ("diario" | "semanal" | "mensal") + params
  - Delegar para o builder correto do cliente
  - Retornar dict padronizado com url, resumo, erro (se houver)

Cada subclasse sobrescreve:
  - CLIENTE_KEY       (str)
  - gerar_diario()    → dict
  - gerar_semanal()   → dict
  - gerar_mensal()    → dict
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class BaseRelatorioAgent:
    """Agente base de relatório — não instanciar diretamente."""

    CLIENTE_KEY: str = ""

    # ── Interface pública ─────────────────────────────────────────────────────

    def gerar(
        self,
        tipo: str = "semanal",
        data_inicio: Optional[date] = None,
        data_fim: Optional[date] = None,
        publicar: bool = True,
        **kwargs,
    ) -> dict:
        """
        Ponto de entrada unificado.

        Args:
            tipo:        "diario" | "semanal" | "mensal"
            data_inicio: início do período (opcional)
            data_fim:    fim do período (opcional)
            publicar:    se True, publica no Notion
            **kwargs:    parâmetros extras específicos do tipo

        Returns:
            dict com: url, resumo, erro (se houver)
        """
        logger.info(
            "[%s] gerar tipo=%s inicio=%s fim=%s",
            self.__class__.__name__, tipo, data_inicio, data_fim,
        )

        try:
            if tipo == "diario":
                data_str = (
                    data_fim.isoformat() if data_fim
                    else kwargs.get("data") or (date.today() - timedelta(days=1)).isoformat()
                )
                return self.gerar_diario(
                    data=data_str,
                    publicar=publicar,
                    **{k: v for k, v in kwargs.items() if k != "data"},
                )
            elif tipo == "mensal":
                return self.gerar_mensal(
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                    publicar=publicar,
                    **kwargs,
                )
            else:  # semanal (default)
                return self.gerar_semanal(
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                    publicar=publicar,
                    **kwargs,
                )
        except Exception as e:
            logger.error("[%s] gerar tipo=%s erro: %s", self.__class__.__name__, tipo, e)
            return {"erro": str(e), "tipo": tipo, "cliente_key": self.CLIENTE_KEY}

    # ── Hooks para subclasses ─────────────────────────────────────────────────

    def gerar_diario(self, data: str = None, publicar: bool = True, **kwargs) -> dict:
        raise NotImplementedError(f"{self.__class__.__name__}.gerar_diario() não implementado")

    def gerar_semanal(
        self,
        data_inicio: Optional[date] = None,
        data_fim: Optional[date] = None,
        publicar: bool = True,
        **kwargs,
    ) -> dict:
        raise NotImplementedError(f"{self.__class__.__name__}.gerar_semanal() não implementado")

    def gerar_mensal(
        self,
        data_inicio: Optional[date] = None,
        data_fim: Optional[date] = None,
        publicar: bool = True,
        **kwargs,
    ) -> dict:
        raise NotImplementedError(f"{self.__class__.__name__}.gerar_mensal() não implementado")
