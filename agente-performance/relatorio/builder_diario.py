"""
relatorio/builder_diario.py

Builder de relatório diário para Notion + Slack.

Gera blocos Notion no mesmo padrão dos relatórios semanais/mensais
e publica no histórico de cada cliente.

Uso:
    from relatorio.builder_diario import gerar_relatorio_diario
    resultado = gerar_relatorio_diario(cliente_key="source_of", plataforma="meta_ads", data="2026-08-14")
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from db.connections import query_fed as query, query_fed_one as query_one
from db.duck import query_duck
from notion.historico import criar_relatorio_no_historico
from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n, roas_emoji
from notion.blocks import h2, h3, paragrafo, divider, callout, bullet, tabela, toggle

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent

LABELS = {
    "source_of":    "Source OF",
    "pod_kombucha": "POD Kombucha",
    "meta_ads":     "Meta Ads",
    "google_ads":   "Google Ads",
}

EMOJIS_CLIENTE = {
    "source_of":    "🔵",
    "pod_kombucha": "🟢",
}






# ── Consultas de dados ────────────────────────────────────────────────────────

def _buscar_resumo_dia(data: str, cliente_key: str, plataforma: str) -> dict | None:
    return query_one("""
        SELECT
            COUNT(DISTINCT campanha)                          AS campanhas,
            ROUND(SUM(impressoes), 0)                        AS impressoes,
            ROUND(SUM(cliques), 0)                           AS cliques,
            ROUND(SUM(alcance), 0)                           AS alcance,
            ROUND(SUM(investimento), 2)                      AS investimento,
            ROUND(SUM(conversoes), 0)                        AS conversoes,
            ROUND(SUM(receita), 2)                           AS receita,
            ROUND(SUM(receita)/NULLIF(SUM(investimento),0), 2) AS roas,
            ROUND(SUM(cliques)/NULLIF(SUM(impressoes),0)*100, 4) AS ctr,
            ROUND(SUM(investimento)/NULLIF(SUM(cliques),0), 2)  AS cpc,
            ROUND(SUM(investimento)/NULLIF(SUM(impressoes),0)*1000, 2) AS cpm
        FROM v_performance
        WHERE data = ? AND cliente_key = ? AND plataforma = ?
    """, (data, cliente_key, plataforma))


def _buscar_campanhas_dia(data: str, cliente_key: str, plataforma: str) -> list[dict]:
    return query("""
        SELECT
            campanha,
            ROUND(investimento, 2)  AS investimento,
            ROUND(receita, 2)       AS receita,
            ROUND(roas, 2)          AS roas,
            ROUND(conversoes, 0)    AS conversoes,
            ROUND(cliques, 0)       AS cliques,
            ROUND(impressoes, 0)    AS impressoes,
            ROUND(ctr, 2)           AS ctr,
            ROUND(cpc_medio, 2)     AS cpc
        FROM v_performance
        WHERE data = ? AND cliente_key = ? AND plataforma = ?
        ORDER BY investimento DESC
    """, (data, cliente_key, plataforma))


def _buscar_shopify_dia(data: str) -> dict | None:
    """Busca dados do dia: pedidos (shopify_receita_diaria) + sessões (shopify_sessoes)."""
    _raw = query_one("""
        SELECT receita, pedidos, novos_clientes, recompradores,
               ticket_medio, total_desconto, pedidos_acima_300
        FROM shopify_receita_diaria
        WHERE data = ?
    """, (data,))
    pedidos = dict(_raw) if _raw else None
    # Sessões via ShopifyQL (sync_shopify_sessions.py)
    try:
        from pathlib import Path
        import sqlite3 as _sqlite3
        _p = Path(__file__).parent.parent / "data/performance/source_of/shopify.db"
        if _p.exists():
            _conn = _sqlite3.connect(str(_p))
            _conn.row_factory = _sqlite3.Row
            _s = _conn.execute("""
                SELECT sessoes, sessoes_com_carrinho, sessoes_checkout,
                       sessoes_compra, taxa_conversao, is_bot_suspect
                FROM source_of_shopify_sessoes WHERE data = ?
            """, (data,)).fetchone()
            _conn.close()
            if _s:
                sess = dict(_s)
                if pedidos:
                    pedidos.update(sess)
                else:
                    pedidos = sess
    except Exception as _e:
        logger.warning("[Shopify] Sessões indisponíveis para %s: %s", data, _e)
    return pedidos


def _buscar_dia_anterior(data: str, cliente_key: str, plataforma: str) -> dict | None:
    """Busca dados do dia anterior para comparativo."""
    d = date.fromisoformat(data) - timedelta(days=1)
    return _buscar_resumo_dia(d.isoformat(), cliente_key, plataforma)


def _buscar_tendencia_7d(data: str, cliente_key: str, plataforma: str) -> dict:
    """
    Retorna média dos últimos 7 dias (excluindo o dia atual) para contexto de tendência.
    Usa DuckDB window functions para calcular em uma única query.
    """
    d_fim = (date.fromisoformat(data) - timedelta(days=1)).isoformat()
    d_ini = (date.fromisoformat(data) - timedelta(days=7)).isoformat()

    rows = query_duck("""
        SELECT
            ROUND(AVG(invest_dia), 2)        AS invest_medio_7d,
            ROUND(AVG(receita_dia), 2)       AS receita_media_7d,
            ROUND(
                SUM(receita_dia) / NULLIF(SUM(invest_dia), 0), 4
            )                                AS roas_medio_7d,
            ROUND(AVG(conversoes_dia), 1)    AS conversoes_media_7d,
            COUNT(*)                         AS dias_com_dados
        FROM (
            SELECT
                data,
                SUM(investimento)  AS invest_dia,
                SUM(receita)       AS receita_dia,
                SUM(conversoes)    AS conversoes_dia
            FROM v_performance
            WHERE data BETWEEN ? AND ?
              AND cliente_key = ?
              AND plataforma  = ?
            GROUP BY data
        ) daily
    """, (d_ini, d_fim, cliente_key, plataforma))
    return rows[0] if rows else {}


def _buscar_top_campanhas_ranking(data: str, cliente_key: str, plataforma: str) -> list[dict]:
    """
    Top 5 campanhas do dia com ranking relativo por ROAS.
    Usa RANK() OVER para indicar posição entre todas as campanhas do dia.
    """
    rows = query_duck("""
        SELECT
            campanha,
            ROUND(investimento, 2)                          AS investimento,
            ROUND(receita, 2)                               AS receita,
            ROUND(roas, 2)                                  AS roas,
            ROUND(conversoes, 0)                            AS conversoes,
            RANK() OVER (ORDER BY roas DESC NULLS LAST)     AS rank_roas,
            COUNT(*) OVER ()                                AS total_campanhas
        FROM v_performance
        WHERE data = ?
          AND cliente_key = ?
          AND plataforma  = ?
          AND investimento > 0
        QUALIFY rank_roas <= 5
        ORDER BY rank_roas
    """, (data, cliente_key, plataforma))
    return rows


# ── Gerador de blocos ─────────────────────────────────────────────────────────

def _blocos_visao_geral(resumo: dict, plataforma: str, data_fmt: str) -> list[dict]:
    invest_imp = round(float(resumo.get("investimento", 0)) * 1.1215, 2) if plataforma == "meta_ads" else None
    roas_e = roas_emoji(resumo.get("roas"))

    linhas = [
        ["💰 Investimento",        fmt_brl(resumo.get("investimento"))],
    ]
    if invest_imp:
        linhas.append(["💰 +Imposto (1,1215x)", fmt_brl(invest_imp)])
    linhas += [
        ["💵 Receita",             fmt_brl(resumo.get("receita"))],
        [f"{roas_e} ROAS",         fmt_x(resumo.get("roas"))],
        ["🛒 Conversões",          fmt_n(resumo.get("conversoes"))],
        ["📊 Campanhas Ativas",    str(resumo.get("campanhas", "—"))],
    ]

    return [
        h2("📊 Visão Geral"),
        tabela(["Métrica", "Resultado"], linhas),
        divider(),
    ]


def _blocos_funil(resumo: dict) -> list[dict]:
    linhas = [
        ["👁️ Impressões",  fmt_n(resumo.get("impressoes")),  "—"],
        ["🖱️ Cliques",     fmt_n(resumo.get("cliques")),     fmt_pct(resumo.get("ctr"))],
        ["✅ Conversões",  fmt_n(resumo.get("conversoes")),  "—"],
    ]
    return [
        h2("🔽 Funil de Conversão"),
        tabela(["Etapa", "Volume", "Taxa"], linhas),
        divider(),
    ]


def _blocos_metricas_avancadas(resumo: dict) -> list[dict]:
    linhas = [
        ["🎯 CTR",      fmt_pct(resumo.get("ctr"))],
        ["💲 CPC",      fmt_brl(resumo.get("cpc"))],
        ["📢 CPM",      fmt_brl(resumo.get("cpm"))],
        ["👥 Alcance",  fmt_n(resumo.get("alcance"))],
    ]
    return [
        h2("📐 Métricas Avançadas"),
        tabela(["Métrica", "Valor"], linhas),
        divider(),
    ]


def _blocos_tendencia_7d(resumo_dia: dict, tend: dict) -> list[dict]:
    """Bloco de contexto: compara o dia vs média dos últimos 7 dias."""
    if not tend or not tend.get("dias_com_dados"):
        return []

    def _delta_emoji(atual, media):
        if not atual or not media:
            return "—", ""
        pct = (atual - media) / media * 100
        if pct >= 10:
            return f"▲ +{pct:.1f}%", "🟢"
        elif pct <= -10:
            return f"▼ {pct:.1f}%", "🔴"
        else:
            return f"→ {pct:+.1f}%", "🟡"

    roas_d, e_roas = _delta_emoji(resumo_dia.get("roas"), tend.get("roas_medio_7d"))
    inv_d,  e_inv  = _delta_emoji(resumo_dia.get("investimento"), tend.get("invest_medio_7d"))
    conv_d, e_conv = _delta_emoji(resumo_dia.get("conversoes"), tend.get("conversoes_media_7d"))

    dias = tend.get("dias_com_dados", 0)
    linhas = [
        ["📈 ROAS",        fmt_x(resumo_dia.get("roas")),        fmt_x(tend.get("roas_medio_7d")),   f"{e_roas} {roas_d}"],
        ["💰 Investimento", fmt_brl(resumo_dia.get("investimento")), fmt_brl(tend.get("invest_medio_7d")), f"{e_inv} {inv_d}"],
        ["🛒 Conversões",  fmt_n(resumo_dia.get("conversoes")),   fmt_n(tend.get("conversoes_media_7d")), f"{e_conv} {conv_d}"],
    ]
    return [
        h2(f"📅 Hoje vs Média {dias}d"),
        tabela(["Métrica", "Hoje", f"Média {dias}d", "Variação"], linhas),
        divider(),
    ]


def _blocos_campanhas(campanhas: list[dict]) -> list[dict]:
    if not campanhas:
        return []

    # Notion limita 100 linhas por tabela (incluindo header)
    MAX_LINHAS = 99
    truncado = len(campanhas) > MAX_LINHAS
    campanhas_exibir = campanhas[:MAX_LINHAS]

    header = ["Campanha", "Invest.", "Receita", "ROAS", "Conv.", "CTR", "CPC"]
    linhas = []
    for c in campanhas_exibir:
        nome = (c.get("campanha") or "—")[:50]
        roas_e = roas_emoji(c.get("roas"))
        linhas.append([
            nome,
            fmt_brl(c.get("investimento")),
            fmt_brl(c.get("receita")),
            f"{roas_e} {fmt_x(c.get('roas'))}",
            fmt_n(c.get("conversoes")),
            fmt_pct(c.get("ctr")),
            fmt_brl(c.get("cpc")),
        ])

    blocos = [
        h2("📋 Desempenho por Campanha"),
        tabela(header, linhas),
    ]
    if truncado:
        blocos.append(paragrafo(f"... e mais {len(campanhas) - MAX_LINHAS} anúncios. Ver Notion para lista completa."))
    blocos.append(divider())
    return blocos


def _blocos_shopify(shopify: dict) -> list[dict]:
    if not shopify:
        return []
    conv_pct = shopify.get("taxa_conversao")
    conv_fmt = f"{conv_pct * 100:.2f}%" if conv_pct is not None else "—"
    bot_warn = " ⚠️ suspeito bot" if shopify.get("is_bot_suspect") else ""
    linhas = [
        ["📦 Pedidos",                fmt_n(shopify.get("pedidos"))],
        ["💰 Receita",                fmt_brl(shopify.get("receita"))],
        ["👁️ Sessões",               f"{fmt_n(shopify.get('sessoes'))}{bot_warn}" if shopify.get('sessoes') else "—"],
        ["🛒 Sessões c/ Checkout",    fmt_n(shopify.get("sessoes_checkout"))],
        ["✅ Sessões c/ Compra",      fmt_n(shopify.get("sessoes_compra"))],
        ["📈 Taxa de Conversão",      conv_fmt],
        ["🆕 Novos Clientes",         fmt_n(shopify.get("novos_clientes"))],
        ["🔁 Recompradores",          fmt_n(shopify.get("recompradores"))],
        ["🏷️ Ticket Médio",          fmt_brl(shopify.get("ticket_medio"))],
        ["🎟️ Total Desconto",        fmt_brl(shopify.get("total_desconto"))],
        ["🏅 Pedidos >R$300",         fmt_n(shopify.get("pedidos_acima_300"))],
    ]
    # Remove linhas com valor None/vazio
    linhas = [l for l in linhas if l[1] and l[1] != "—"]
    return [
        h2("🛒 Shopify — Dados da Loja"),
        tabela(["Métrica", "Valor"], linhas),
        divider(),
    ]


def _blocos_comparativo(resumo: dict, anterior: dict) -> list[dict]:
    """Comparativo dia vs dia anterior."""
    def _var(atual, ant, maior_melhor=True) -> str:
        if atual is None or ant is None or float(ant) == 0:
            return "—"
        v = (float(atual) - float(ant)) / float(ant) * 100
        seta = ("↑" if v > 0 else "↓") if maior_melhor else ("↓" if v > 0 else "↑")
        return f"{seta} {abs(v):.1f}%"

    linhas = [
        ["💰 Investimento", fmt_brl(anterior.get("investimento")), fmt_brl(resumo.get("investimento")),
         _var(resumo.get("investimento"), anterior.get("investimento"), False)],
        ["💵 Receita",      fmt_brl(anterior.get("receita")),      fmt_brl(resumo.get("receita")),
         _var(resumo.get("receita"), anterior.get("receita"))],
        ["ROAS",            fmt_x(anterior.get("roas")),           fmt_x(resumo.get("roas")),
         _var(resumo.get("roas"), anterior.get("roas"))],
        ["🛒 Conversões",   fmt_n(anterior.get("conversoes")),     fmt_n(resumo.get("conversoes")),
         _var(resumo.get("conversoes"), anterior.get("conversoes"))],
        ["🎯 CTR",          fmt_pct(anterior.get("ctr")),          fmt_pct(resumo.get("ctr")),
         _var(resumo.get("ctr"), anterior.get("ctr"))],
    ]
    return [
        h2("📈 Comparativo Dia Anterior"),
        tabela(["Métrica", "Ontem", "Hoje", "Var."], linhas),
        divider(),
    ]


def _blocos_alertas(campanhas: list[dict]) -> list[dict]:
    alertas = []
    for c in campanhas:
        roas = c.get("roas")
        invest = float(c.get("investimento") or 0)
        conv = float(c.get("conversoes") or 0)
        nome = (c.get("campanha") or "—")[:50]

        if invest > 50 and conv == 0:
            alertas.append(f"⚠️ {nome} — R$ {invest:.0f} investidos sem nenhuma conversão")
        elif roas is not None and float(roas) < 1.5 and invest > 100:
            alertas.append(f"🔴 {nome} — ROAS {roas:.2f}x abaixo do breakeven (invest R$ {invest:.0f})")

    if not alertas:
        return [h2("✅ Alertas"), paragrafo("Nenhum alerta crítico no período."), divider()]

    blocos = [h2("⚠️ Alertas")]
    for a in alertas:
        blocos.append(bullet(a))
    blocos.append(divider())
    return blocos


# ── Função principal ──────────────────────────────────────────────────────────

def gerar_relatorio_diario(
    cliente_key: str,
    plataforma: str = None,
    data: str = None,
    publicar: bool = True,
    data_inicio=None,
    data_fim=None,
) -> dict:
    """
    Gera relatório diário para um cliente+plataforma e publica no Notion.

    Args:
        cliente_key: 'source_of' | 'pod_kombucha'
        plataforma:  'meta_ads' | 'google_ads' (padrão: detecta automaticamente)
        data:        'YYYY-MM-DD' (padrão: ontem)
        publicar:    se False, retorna blocos sem publicar
        data_inicio: date (alternativa a data, usa data_fim como referência)
        data_fim:    date (alternativa a data)

    Returns:
        dict com url, page_id, resumo
    """
    from datetime import date as _date

    # Reseta conexão federada para garantir que dados recém-sincronizados são vistos
    # (a conexão :memory: é singleton e não detecta writes nos DBs de performance feitos
    # após sua inicialização — reinicializar leva ~1.4ms e é seguro)
    try:
        from db.connections import reset_federated_conn
        reset_federated_conn()
    except Exception:
        pass
    # Resolve data a partir de data_fim ou data_inicio se passados pelo cron
    if data is None:
        if data_fim is not None:
            data = data_fim.isoformat() if hasattr(data_fim, 'isoformat') else str(data_fim)
        else:
            data = (_date.today() - timedelta(days=1)).isoformat()

    # Detecta plataforma automaticamente se não informada
    if plataforma is None:
        from db.connections import query_fed_one as _q
        for _plat in ("meta_ads", "google_ads"):
            _r = _buscar_resumo_dia(data, cliente_key, _plat)
            if _r and _r.get("investimento"):
                plataforma = _plat
                break
        if plataforma is None:
            plataforma = "meta_ads"  # fallback

    resumo = _buscar_resumo_dia(data, cliente_key, plataforma)
    if not resumo or not resumo.get("investimento"):
        logger.warning("[Diário] Sem dados para %s·%s em %s", cliente_key, plataforma, data)
        return {"erro": "sem_dados", "data": data, "cliente_key": cliente_key, "plataforma": plataforma}

    campanhas = _buscar_campanhas_dia(data, cliente_key, plataforma)
    anterior  = _buscar_dia_anterior(data, cliente_key, plataforma)
    tendencia = _buscar_tendencia_7d(data, cliente_key, plataforma)

    shopify = None
    # Shopify só aparece no relatório Meta Ads (plataforma principal de receita)
    # Não duplicar na thread do Google Ads
    if cliente_key == "source_of" and plataforma == "meta_ads":
        shopify = _buscar_shopify_dia(data)

    # Formata data
    data_fmt  = f"{data[8:10]}/{data[5:7]}/{data[:4]}"
    nome_c    = LABELS.get(cliente_key, cliente_key)
    nome_p    = LABELS.get(plataforma, plataforma)
    emoji_c   = EMOJIS_CLIENTE.get(cliente_key, "📊")

    titulo = f"{emoji_c} {nome_c} · {nome_p} · Relatório Diário · {data_fmt}"

    # ── Monta blocos Notion ───────────────────────────────────────────────
    blocos = [
        callout(
            f"{nome_c} · {nome_p} · {data_fmt} — "
            f"ROAS: {fmt_x(resumo.get('roas'))} · "
            f"Invest: {fmt_brl(resumo.get('investimento'))} · "
            f"Receita: {fmt_brl(resumo.get('receita'))} · "
            f"Conv: {fmt_n(resumo.get('conversoes'))}",
            emoji=emoji_c,
        ),
        divider(),
    ]

    blocos += _blocos_visao_geral(resumo, plataforma, data_fmt)
    blocos += _blocos_funil(resumo)
    blocos += _blocos_metricas_avancadas(resumo)
    blocos += _blocos_tendencia_7d(resumo, tendencia)

    if anterior:
        blocos += _blocos_comparativo(resumo, anterior)

    blocos += _blocos_campanhas(campanhas)
    blocos += _blocos_alertas(campanhas)

    if shopify:
        blocos += _blocos_shopify(shopify)

    # ── Insights & Ações via Claude ───────────────────────────────────────
    try:
        from relatorio.insights import gerar_insights
        dados_extras = {"shopify": shopify} if shopify else None
        blocos += gerar_insights(
            resumo=resumo,
            campanhas=campanhas,
            periodo="diario",
            cliente_key=cliente_key,
            plataforma=plataforma,
            periodo_str=data_fmt,
            dados_extras=dados_extras,
        )
    except Exception as e:
        logger.warning("[Diário] Insights falhou: %s", e)

    blocos.append(paragrafo(
        f"Gerado em {time.strftime('%d/%m/%Y %H:%M')} · Dados via API direta: {nome_p}",
        bold=False
    ))

    if not publicar:
        return {
            "titulo":     titulo,
            "resumo":     resumo,
            "blocos":     blocos,
            "campanhas":  campanhas,
            "anterior":   anterior,
            "shopify":    shopify,
            "data":       data,
            "cliente_key": cliente_key,
            "plataforma": plataforma,
        }

    # ── Publica no Notion ─────────────────────────────────────────────────
    from datetime import date as _date
    data_obj = _date.fromisoformat(data)
    resultado = criar_relatorio_no_historico(
        titulo=titulo,
        blocos=blocos,
        periodo_str=data_fmt,
        tipo="diario",
        data_ref=data_obj,
        data_inicio=data_obj,
        data_fim=data_obj,
        cliente_key=cliente_key,
        plataforma=plataforma,
    )

    logger.info("[Diário] %s·%s %s → Notion: %s", cliente_key, plataforma, data, resultado.get("url"))

    # ── Persiste log do relatório no SQLite ──────────────────────────────
    try:
        from db.relatorio_log import registrar_relatorio
        registrar_relatorio(
            cliente_key=cliente_key,
            plataforma=plataforma,
            periodicidade="diario",
            data_inicio=data,
            data_fim=data,
            titulo=titulo,
            notion_url=resultado.get("url"),
            notion_page_id=resultado.get("page_id"),
            blocos_n=len(blocos),
            resumo=resumo,
            status="ok",
        )
    except Exception as _log_err:
        logger.warning("[Diário] Falha ao registrar log de relatório: %s", _log_err)

    return {
        "titulo":      titulo,
        "page_id":     resultado.get("page_id"),
        "url":         resultado.get("url"),
        "resumo":      resumo,
        "blocos":      blocos,
        "campanhas":   campanhas,
        "anterior":    anterior,
        "shopify":     shopify,
        "data":        data,
        "cliente_key": cliente_key,
        "plataforma":  plataforma,
    }


def gerar_relatorio_consolidado(
    cliente_key: str,
    plataformas: list[str],
    data: str,
    publicar: bool = True,
) -> dict:
    """
    Gera um relatório diário consolidado (todas as plataformas numa só página Notion).

    Args:
        cliente_key:  'source_of' | 'pod_kombucha'
        plataformas:  lista de plataformas com dados, ex: ['google_ads', 'meta_ads']
        data:         'YYYY-MM-DD'
        publicar:     se False, retorna blocos sem publicar no Notion

    Returns:
        dict com url, page_id, resumo (resumo da plataforma principal), blocos
    """
    try:
        from db.connections import reset_federated_conn
        reset_federated_conn()
    except Exception:
        pass

    from datetime import date as _date
    data_obj  = _date.fromisoformat(data)
    data_fmt  = f"{data[8:10]}/{data[5:7]}/{data[:4]}"
    nome_c    = LABELS.get(cliente_key, cliente_key)
    emoji_c   = EMOJIS_CLIENTE.get(cliente_key, "📊")

    plat_labels = {"meta_ads": "Meta Ads", "google_ads": "Google Ads"}
    plats_str   = " + ".join(plat_labels.get(p, p) for p in plataformas if p != "shopify")
    titulo      = f"{emoji_c} {nome_c} · {plats_str} · Relatório Diário · {data_fmt}"

    blocos = [
        callout(
            f"{nome_c} · {plats_str} · {data_fmt}",
            emoji=emoji_c,
        ),
        divider(),
    ]

    resumo_principal = None  # resumo da primeira plataforma de anúncios

    for plataforma in [p for p in plataformas if p != "shopify"]:
        resumo = _buscar_resumo_dia(data, cliente_key, plataforma)
        if not resumo or not resumo.get("investimento"):
            logger.warning("[Consolidado] Sem dados para %s·%s em %s", cliente_key, plataforma, data)
            continue

        if resumo_principal is None:
            resumo_principal = resumo

        campanhas = _buscar_campanhas_dia(data, cliente_key, plataforma)
        anterior  = _buscar_dia_anterior(data, cliente_key, plataforma)
        tendencia = _buscar_tendencia_7d(data, cliente_key, plataforma)
        nome_p    = plat_labels.get(plataforma, plataforma)

        # Cabeçalho da seção
        blocos.append(h2(f"📊 {nome_p}"))
        blocos += _blocos_visao_geral(resumo, plataforma, data_fmt)
        blocos += _blocos_funil(resumo)
        blocos += _blocos_metricas_avancadas(resumo)
        blocos += _blocos_tendencia_7d(resumo, tendencia)
        if anterior:
            blocos += _blocos_comparativo(resumo, anterior)
        blocos += _blocos_campanhas(campanhas)
        blocos += _blocos_alertas(campanhas)
        blocos.append(divider())

    # Shopify (se disponível) — seção única após todas as plataformas de anúncios
    if "shopify" in plataformas or cliente_key == "source_of":
        shopify = _buscar_shopify_dia(data)
        if shopify:
            blocos += _blocos_shopify(shopify)

    # Insights consolidados via Claude
    try:
        from relatorio.insights import gerar_insights
        plat_principal = next((p for p in plataformas if p != "shopify"), "meta_ads")
        blocos += gerar_insights(
            resumo=resumo_principal or {},
            campanhas=[],
            periodo="diario",
            cliente_key=cliente_key,
            plataforma=plat_principal,
            periodo_str=data_fmt,
            dados_extras=None,
        )
    except Exception as e:
        logger.warning("[Consolidado] Insights falhou: %s", e)

    blocos.append(paragrafo(
        f"Gerado em {time.strftime('%d/%m/%Y %H:%M')} · Dados via API: {plats_str}",
        bold=False
    ))

    if not resumo_principal:
        return {"erro": "sem_dados", "data": data, "cliente_key": cliente_key}

    if not publicar:
        return {
            "titulo":      titulo,
            "resumo":      resumo_principal,
            "blocos":      blocos,
            "data":        data,
            "cliente_key": cliente_key,
            "plataforma":  "consolidado",
        }

    # Publica no Notion (página única, sem sufixo de plataforma)
    resultado = criar_relatorio_no_historico(
        titulo=titulo,
        blocos=blocos,
        periodo_str=data_fmt,
        tipo="diario",
        data_ref=data_obj,
        data_inicio=data_obj,
        data_fim=data_obj,
        cliente_key=cliente_key,
        plataforma="consolidado",  # chave única por cliente+data (não por plataforma)
    )

    logger.info("[Consolidado] %s %s → Notion: %s", cliente_key, data, resultado.get("url"))

    try:
        from db.relatorio_log import registrar_relatorio
        registrar_relatorio(
            cliente_key=cliente_key,
            plataforma="consolidado",
            periodicidade="diario",
            data_inicio=data,
            data_fim=data,
            titulo=titulo,
            notion_url=resultado.get("url"),
            notion_page_id=resultado.get("page_id"),
            blocos_n=len(blocos),
            resumo=resumo_principal,
            status="ok",
        )
    except Exception as _log_err:
        logger.warning("[Consolidado] Falha ao registrar log: %s", _log_err)

    return {
        "titulo":      titulo,
        "page_id":     resultado.get("page_id"),
        "url":         resultado.get("url"),
        "resumo":      resumo_principal,
        "blocos":      blocos,
        "data":        data,
        "cliente_key": cliente_key,
        "plataforma":  "consolidado",
    }
