"""
agent/clients/relatorio/source_of.py

SourceOFRelatorioAgent — subagente de relatório da Source OF.

⚠️ REGRA PERMANENTE:
  Source OF tem: Meta Ads ✅ + Google Ads ✅ + Shopify ✅
  É o único cliente com Shopify integrado.

Plataformas suportadas nos relatórios:
  - Diário:  meta_ads | google_ads (detecta automaticamente)
  - Semanal: meta_ads (principal) + inclui dados Shopify quando disponível
  - Mensal:  meta_ads + dados Shopify + análise de recompra

Delega para:
  - relatorio/builder_diario.py   → gerar_relatorio_diario()
  - relatorio/builder_semanal.py  → gerar_relatorio_semanal()
  - relatorio/builder_mensal.py   → gerar_relatorio_mensal()
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from agent.clients.relatorio.base import BaseRelatorioAgent

logger = logging.getLogger(__name__)


class SourceOFRelatorioAgent(BaseRelatorioAgent):
    """Subagente de relatório — Source OF (Meta Ads + Google Ads + Shopify)."""

    CLIENTE_KEY = "source_of"

    def gerar_diario(
        self,
        data: str = None,
        publicar: bool = True,
        plataforma: str = None,
        **kwargs,
    ) -> dict:
        """
        Gera relatório diário da Source OF.

        Args:
            data:       'YYYY-MM-DD' (default: ontem)
            publicar:   se True, publica no Notion
            plataforma: 'meta_ads' | 'google_ads' (default: detecta automaticamente)
        """
        from relatorio.builder_diario import gerar_relatorio_diario
        return gerar_relatorio_diario(
            cliente_key=self.CLIENTE_KEY,
            plataforma=plataforma,
            data=data,
            publicar=publicar,
        )

    def gerar_semanal(
        self,
        data_inicio: Optional[date] = None,
        data_fim: Optional[date] = None,
        publicar: bool = True,
        **kwargs,
    ) -> dict:
        """
        Gera relatório semanal da Source OF (Meta Ads + Shopify).

        Args:
            data_inicio: início do período (default: segunda-feira da semana passada)
            data_fim:    fim do período (default: domingo da semana passada)
            publicar:    se True, publica no Notion
        """
        from relatorio.builder_semanal import gerar_relatorio_semanal
        return gerar_relatorio_semanal(
            cliente="source_of",
            data_inicio=data_inicio,
            data_fim=data_fim,
            publicar=publicar,
        )

    def gerar_mensal(
        self,
        data_inicio: Optional[date] = None,
        data_fim: Optional[date] = None,
        publicar: bool = True,
        ano: int = None,
        mes: int = None,
        **kwargs,
    ) -> dict:
        """
        Gera relatório mensal da Source OF (Meta Ads + Shopify + análise recompra).

        Args:
            data_inicio: primeiro dia do mês (alternativa a ano/mes)
            data_fim:    último dia do mês (alternativa a ano/mes)
            publicar:    se True, publica no Notion
            ano:         ano de referência (default: mês passado)
            mes:         mês de referência 1–12 (default: mês passado)
        """
        from relatorio.builder_mensal import gerar_relatorio_mensal

        # Suporta tanto data_inicio/data_fim quanto ano/mes
        if data_inicio and not ano:
            ano = data_inicio.year
            mes = data_inicio.month

        return gerar_relatorio_mensal(
            cliente="source_of",
            ano=ano,
            mes=mes,
            publicar=publicar,
        )
