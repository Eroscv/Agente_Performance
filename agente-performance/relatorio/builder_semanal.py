"""
relatorio/builder_semanal.py

Gerador de relatório semanal Meta Ads.
Inclui _blocos_tendencia_semanal e função pública gerar_relatorio_semanal
(que também orquestra o relatório mensal quando tipo="mensal").
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from relatorio.builder_shared import (
    # constantes
    CLIENTES, DB_PATH, _SQLITE_TIMEOUT, _MESES_PT,
    # db
    _buscar_campanhas, _resumo_geral, _alertas,
    # fmt
    _fmt_brl, _fmt_x, _fmt_pct, _fmt_n,
    # blocos compartilhados
    _blocos_resumo, _blocos_tabela_campanhas, _blocos_top_campanhas,
    _blocos_alocacao, _blocos_alertas, _blocos_comparativo, _blocos_funil,
    _blocos_metricas_site_semanal, _blocos_alocacao_canais, _blocos_alocacao_por_anuncio,
    _blocos_placeholder, _blocos_pedidos_sku, _blocos_pedidos_estado, _blocos_recompradoras,
    _buscar_dados_diarios,
    # helpers mensais
    _blocos_analise_ultima_semana, _blocos_visao_mensal_diaria, _blocos_visao_mensal_narrativa,
    _blocos_metas_mes, _blocos_proximos_passos, _blocos_tendencia_mensal,
)

from relatorio.regras import spend_com_imposto
from notion.historico import criar_relatorio_no_historico

logger = logging.getLogger(__name__)

def _blocos_tendencia_semanal(semanas: int = 4) -> list[dict]:
    """
    Tendencia das ultimas N semanas (WoW) no relatorio Notion.
    """
    try:
        from agent.comparatives import comparar_semana_a_semana
        serie = comparar_semana_a_semana(semanas=semanas)
    except Exception as e:
        logger.warning("[Builder] _blocos_tendencia_semanal falhou: %s", e)
        return []
    if not serie:
        return []
    header = ["Semana", "Invest.", "Receita", "ROAS", "Compras", "CTR", "CPC", "D ROAS", "D Receita"]
    def _fmt_wow(v):
        if v is None: return ""
        sign = "+" if v > 0 else ""
        arrow = "UP" if v > 5 else ("DOWN" if v < -5 else "->")
        return f"{arrow} {sign}{v:.1f}%"
    table_rows = [{"type":"table_row","table_row":{"cells":[[{"text":{"content":h},"annotations":{"bold":True}}] for h in header]}}]
    for s in serie:
        table_rows.append({"type":"table_row","table_row":{"cells":[
            [{"text":{"content":s.get("semana","")}}],
            [{"text":{"content":_fmt_brl(s.get("investimento"))}}],
            [{"text":{"content":_fmt_brl(s.get("receita"))}}],
            [{"text":{"content":_fmt_x(s.get("roas"))}}],
            [{"text":{"content":_fmt_n(s.get("compras"))}}],
            [{"text":{"content":_fmt_pct(s.get("ctr"))}}],
            [{"text":{"content":_fmt_brl(s.get("cpc"))}}],
            [{"text":{"content":_fmt_wow(s.get("roas_wow_pct"))}}],
            [{"text":{"content":_fmt_wow(s.get("receita_wow_pct"))}}],
        ]}})
    return [
        {"object":"block","type":"heading_2","heading_2":{"rich_text":[{"text":{"content":f"Tendencia Ultimas {semanas} Semanas (WoW)"}}]}},
        {"object":"block","type":"table","table":{"table_width":len(header),"has_column_header":True,"has_row_header":False,"children":table_rows}},
        {"object":"block","type":"divider","divider":{}},
    ]


def gerar_relatorio_semanal(
    cliente: str = "source_of",
    data_inicio: date = None,
    data_fim: date = None,
    publicar: bool = True,
    db_path: Path = DB_PATH,
    tipo: str = "semanal",
) -> dict:
    """
    Gera e publica o relatório semanal (ou mensal) Meta Ads.

    Args:
        cliente:      chave do cliente (ex: 'source_of')
        data_inicio:  início do período (default: segunda-feira da semana passada)
        data_fim:     fim do período (default: domingo da semana passada)
        publicar:     se True, publica no Notion
        db_path:      caminho do SQLite
        tipo:         "semanal" (padrão) ou "mensal" — altera comparativo, tendência,
                      alocação e título

    Returns:
        dict com: page_id, url, resumo, num_campanhas, alertas
    """
    # Dados vêm do SQLite (sync diário via API Meta/Google — ver scripts/sync_*_diario.py)


    # ── Período ───────────────────────────────────────────────────────────
    if data_inicio is None or data_fim is None:
        hoje    = date.today()
        seg_ant = hoje - timedelta(days=hoje.weekday() + 7)
        dom_ant = seg_ant + timedelta(days=6)
        data_inicio = data_inicio or seg_ant
        data_fim    = data_fim    or dom_ant

    cliente_nome = CLIENTES.get(cliente, cliente)

    # ── Título e período anterior ─────────────────────────────────────────
    if tipo == "mensal":
        # Título: "📊 Source OF · Meta Ads Mensal · Junho/2026"
        mes_pt = _MESES_PT[data_inicio.month]
        periodo_str = f"{mes_pt}/{data_inicio.year}"
        titulo_periodo = periodo_str

        # Período anterior = mês anterior completo
        primeiro_atual = data_inicio  # já é dia 1
        ant_fim = primeiro_atual - timedelta(days=1)       # último dia do mês anterior
        ant_inicio = ant_fim.replace(day=1)                # primeiro dia do mês anterior
        periodo_anterior_str = f"{_MESES_PT[ant_inicio.month]}/{ant_inicio.year}"
    else:
        periodo_str = f"{data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"
        titulo_periodo = periodo_str

        # Período anterior: mesma duração
        delta_dias = (data_fim - data_inicio).days + 1
        ant_fim    = data_inicio - timedelta(days=1)
        ant_inicio = ant_fim - timedelta(days=delta_dias - 1)
        periodo_anterior_str = f"{ant_inicio.strftime('%d/%m/%Y')} → {ant_fim.strftime('%d/%m/%Y')}"

    logger.info("[Builder] Gerando relatório %s · %s · tipo=%s", cliente_nome, periodo_str, tipo)

    # ── Validação dos dados ──────────────────────────────────────────────────
    try:
        from db.validator import validar
        val_report = validar(db_path)
        if not val_report.ok:
            logger.error("[Builder] Dados com ERRORs de validação — relatório pode conter inconsistências")
        elif val_report.warnings:
            logger.warning("[Builder] Dados com %d aviso(s) de validação", len(val_report.warnings))
    except Exception as e:
        logger.warning("[Builder] Falha na validação pré-relatório: %s", e)
        val_report = None

    # ── Dados ─────────────────────────────────────────────────────────────
    campanhas = _buscar_campanhas(data_inicio, data_fim, db_path)
    if not campanhas:
        logger.warning("[Builder] Nenhuma campanha encontrada no período %s", periodo_str)
        return {"erro": "Nenhuma campanha encontrada", "periodo": periodo_str}

    resumo  = _resumo_geral(campanhas)

    # ── Receita Shopify (real) para o período atual e anterior ───────────────
    try:
        import sqlite3 as _sq
        _conn = _sq.connect(str(db_path))
        def _shopify_receita_periodo(ini, fim):
            row = _conn.execute(
                "SELECT COALESCE(SUM(receita),0) AS r FROM shopify_receita_diaria "
                "WHERE data >= ? AND data <= ?",
                (str(ini), str(fim))
            ).fetchone()
            return float(row[0]) if row and row[0] else None
        resumo["receita_shopify"] = _shopify_receita_periodo(data_inicio, data_fim)
        _receita_shopify_ant       = _shopify_receita_periodo(ant_inicio, ant_fim)
        _conn.close()
        logger.info("[Builder] Receita Shopify atual=%s anterior=%s",
                    resumo['receita_shopify'], _receita_shopify_ant)
    except Exception as _e:
        logger.warning("[Builder] Falha ao buscar receita Shopify: %s", _e)
        resumo["receita_shopify"] = None
        _receita_shopify_ant = None

    # ── Dados período anterior ─────────────────────────────────────────────
    campanhas_anterior = _buscar_campanhas(ant_inicio, ant_fim, db_path)
    resumo_anterior    = _resumo_geral(campanhas_anterior) if campanhas_anterior else {}
    resumo_anterior["receita_shopify"] = _receita_shopify_ant

    alertas = _alertas(campanhas, resumo, resumo_anterior=resumo_anterior)

    logger.info("[Builder] %d campanhas · Invest.: %s · ROAS: %s",
                len(campanhas), _fmt_brl(resumo["total_invest"]), _fmt_x(resumo["roas"]))

    # ── Blocos Notion ──────────────────────────────────────────────────────
    if tipo == "mensal":
        mes_pt  = _MESES_PT[data_inicio.month]
        titulo  = f"Source Of Report - Mensal {mes_pt} {data_inicio.year}"
    else:
        titulo = f"📊 {cliente_nome} · Meta Ads · {titulo_periodo}"

    # Callout de geração automática
    callout_gerado = {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": [{"text": {"content":
                f"Relatório gerado automaticamente pelo Agente de Performance Guará · {periodo_str}"
            }}],
            "icon": {"type": "emoji", "emoji": "⚙️"},
        }
    }

    if tipo == "mensal":
        # ── Estrutura do relatório mensal ────────────────────────────────────
        # Última semana do mês (igual ao semanal) + consolidado do mês
        blocos = [callout_gerado]

        # Aviso de qualidade de dados — apenas ERRORs reais (warnings operacionais omitidos)
        if val_report and not val_report.ok:
            errors_txt = "\n".join(
                f"❌ {i.check}: {i.message}"
                for i in val_report.issues
                if i.severity == 'ERROR'
            )
            if errors_txt:
                blocos.append({
                    "object": "block", "type": "callout",
                    "callout": {
                        "rich_text": [{"text": {"content": f"⚠️ Qualidade dos dados\n{errors_txt}"}}],
                        "icon": {"type": "emoji", "emoji": "⚠️"},
                        "color": "yellow_background",
                    }
                })

        # ── Dados Shopify — última semana do mês ─────────────────────────
        sem_fim = data_fim
        sem_ini = data_fim - timedelta(days=6)
        dados_sku = dados_estado = dados_recompras = dados_assiduidade = dados_metricas_site = None
        logger.info("[Builder] Dados Shopify detalhados indisponíveis — usando placeholder")

        # 1. Funil de Conversão
        blocos += _blocos_funil(resumo)

        # 2. Métricas de Site (Shopify)
        blocos += _blocos_metricas_site_semanal(
            data_inicio=data_inicio,
            data_fim=data_fim,
            shopify=dados_metricas_site,
            invest_meta=resumo.get('total_invest'),
        )

        # 3. Alocação Budget por Canal
        blocos += _blocos_alocacao_canais(resumo)

        # 4. Alocação Budget por Anúncio
        blocos += _blocos_alocacao(campanhas)

        # 5. Pedidos por SKU
        blocos += _blocos_pedidos_sku(dados_sku)

        # 6. Pedidos por Estado
        blocos += _blocos_pedidos_estado(dados_estado)

        # 7. Recuperação de Carrinho (placeholder)
        blocos += _blocos_placeholder(
            "Recuperação de Carrinho Abandonado", "🔄",
            "⏳ Dados de recuperação de carrinho pendentes.",
        )

        # 8. Pedidos Orgânicos Instagram (placeholder)
        blocos += _blocos_placeholder(
            "Pedidos Orgânicos Instagram", "📸",
            "⏳ Dados de pedidos orgânicos pendentes.",
        )

        # 9. Base de Recompradoras
        blocos += _blocos_recompradoras(
            dados_recompras_sku=dados_recompras,
            dados_assiduidade=dados_assiduidade,
        )

        # 10. Comparativo com Período Anterior
        if resumo_anterior:
            blocos += _blocos_comparativo(resumo, resumo_anterior, periodo_anterior_str)

    else:
        # ── Estrutura do relatório semanal ──────────────────────────────────
        blocos = []

        # Aviso de qualidade de dados — apenas ERRORs reais
        if val_report and not val_report.ok:
            errors_txt = "\n".join(
                f"❌ {i.check}: {i.message}"
                for i in val_report.issues
                if i.severity == 'ERROR'
            )
            if errors_txt:
                blocos.append({
                    "object": "block", "type": "callout",
                    "callout": {
                        "rich_text": [{"text": {"content": f"⚠️ Qualidade dos dados\n{errors_txt}"}}],
                        "icon": {"type": "emoji", "emoji": "⚠️"},
                        "color": "yellow_background",
                    }
                })

        dados_sku = dados_estado = dados_recompras = dados_assiduidade = dados_metricas_site = None

        # 1. Funil de Conversão
        blocos += _blocos_funil(resumo)

        # 2. Métricas de Site (Shopify)
        blocos += _blocos_metricas_site_semanal(
            data_inicio=data_inicio,
            data_fim=data_fim,
            shopify=dados_metricas_site,
            invest_meta=resumo.get('total_invest'),
        )

        # 3. Alocação Budget por Canal
        blocos += _blocos_alocacao_canais(resumo)

        # 4. Alocação Budget por Anúncio
        blocos += _blocos_alocacao_por_anuncio(data_inicio, data_fim, db_path)

        # 5. Pedidos por SKU
        blocos += _blocos_pedidos_sku(dados_sku)

        # 6. Pedidos por Estado
        blocos += _blocos_pedidos_estado(dados_estado)

        # 7. Recuperação de Carrinho (placeholder)
        blocos += _blocos_placeholder(
            "Recuperação de Carrinho Abandonado", "🔄",
            "⏳ Dados de recuperação de carrinho pendentes.",
        )

        # 8. Pedidos Orgânicos Instagram (placeholder)
        blocos += _blocos_placeholder(
            "Pedidos Orgânicos Instagram", "📸",
            "⏳ Dados de pedidos orgânicos pendentes.",
        )

        # 9. Base de Recompradoras
        blocos += _blocos_recompradoras(
            dados_recompras_sku=dados_recompras,
            dados_assiduidade=dados_assiduidade,
        )

        # 10. Comparativo com Período Anterior
        if resumo_anterior:
            blocos += _blocos_comparativo(resumo, resumo_anterior, periodo_anterior_str)

        # 11. Tendência WoW
        blocos += _blocos_tendencia_semanal()


    # ── Insights & Ações via Claude ──────────────────────────────────────────
    try:
        from relatorio.insights import gerar_insights
        periodo_insights = "mensal" if tipo == "mensal" else "semanal"
        blocos += gerar_insights(
            resumo=resumo,
            campanhas=campanhas,
            periodo=periodo_insights,
            cliente_key=cliente,
            plataforma="meta_ads",
            periodo_str=periodo_str,
        )
    except Exception as e:
        logger.warning("[Semanal] Insights falhou: %s", e)

    # ── Publicação ─────────────────────────────────────────────────────────
    if not publicar:
        logger.info("[Builder] publicar=False — relatório gerado em memória")
        return {
            "titulo": titulo,
            "periodo": periodo_str,
            "resumo": resumo,
            "alertas": alertas,
            "num_campanhas": len(campanhas),
            "blocos": blocos,
        }

    resultado = criar_relatorio_no_historico(
        titulo=titulo,
        blocos=blocos,
        periodo_str=periodo_str,
        tipo=tipo,
        data_ref=data_fim,
        data_inicio=data_inicio,
        data_fim=data_fim,
        cliente_key=cliente,
    )

    return {
        "titulo":        titulo,
        "periodo":       periodo_str,
        "page_id":       resultado.get("page_id"),
        "url":           resultado.get("url"),
        "resumo":        resumo,
        "alertas":       alertas,
        "num_campanhas": len(campanhas),
    }


