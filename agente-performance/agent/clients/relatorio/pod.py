"""
agent/clients/relatorio/pod.py

PODRelatorioAgent — subagente de relatório da POD Kombucha.

⚠️ REGRA PERMANENTE:
  POD Kombucha tem: Meta Ads ✅ + Google Ads ⏳ (inativo, aguardando customer_id)
  POD NÃO tem Shopify — nunca injetar dados Shopify aqui.

Estrutura inicial espelhada da Source OF (SourceOFRelatorioAgent).
Ajustes específicos da POD serão feitos conforme necessidade.

Delega para:
  - relatorio/builder_diario.py  → gerar_relatorio_diario(cliente_key='pod_kombucha')
  - relatorio/builder_pod.py     → gerar_relatorio_pod() (semanal + mensal)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from agent.clients.relatorio.base import BaseRelatorioAgent

logger = logging.getLogger(__name__)


class PODRelatorioAgent(BaseRelatorioAgent):
    """Subagente de relatório — POD Kombucha (Meta Ads)."""

    CLIENTE_KEY = "pod_kombucha"

    def gerar_diario(
        self,
        data: str = None,
        publicar: bool = True,
        plataforma: str = "meta_ads",
        **kwargs,
    ) -> dict:
        """
        Gera relatório diário da POD Kombucha.

        Args:
            data:       'YYYY-MM-DD' (default: ontem)
            publicar:   se True, publica no Notion
            plataforma: 'meta_ads' (padrão — Google Ads inativo na POD)
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
        Gera relatório semanal da POD Kombucha (Meta Ads).

        Args:
            data_inicio: início do período (default: segunda-feira da semana passada)
            data_fim:    fim do período (default: domingo da semana passada)
            publicar:    se True, publica no Notion
        """
        from relatorio.builder_pod import gerar_relatorio_pod
        return gerar_relatorio_pod(
            data_inicio=data_inicio,
            data_fim=data_fim,
            tipo="semanal",
            publicar=publicar,
        )

    def gerar_mensal(
        self,
        data_inicio: Optional[date] = None,
        data_fim: Optional[date] = None,
        publicar: bool = True,
        **kwargs,
    ) -> dict:
        """
        Gera relatório mensal da POD Kombucha (Meta Ads).

        Args:
            data_inicio: primeiro dia do mês (default: mês passado)
            data_fim:    último dia do mês (default: mês passado)
            publicar:    se True, publica no Notion
        """
        from relatorio.builder_pod import gerar_relatorio_pod

        # Resolve datas padrão se não passadas
        if data_inicio is None or data_fim is None:
            from datetime import date as _d
            from calendar import monthrange
            hoje = _d.today()
            primeiro_este = hoje.replace(day=1)
            ultimo_mes = primeiro_este - __import__("datetime").timedelta(days=1)
            _, n_dias = monthrange(ultimo_mes.year, ultimo_mes.month)
            data_inicio = data_inicio or _d(ultimo_mes.year, ultimo_mes.month, 1)
            data_fim    = data_fim    or _d(ultimo_mes.year, ultimo_mes.month, n_dias)

        return gerar_relatorio_pod(
            data_inicio=data_inicio,
            data_fim=data_fim,
            tipo="mensal",
            publicar=publicar,
        )
