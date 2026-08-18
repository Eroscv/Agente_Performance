"""
slack/helpers.py

Funções utilitárias dos handlers Slack:
  - _detectar_formato_export()    — detecta excel/pdf/notion no texto
  - formatar_resumo_relatorio()   — monta texto de resumo
  - _sugestao_proativa()          — sugere próximo passo por intent
  - _verificar_cobertura_dados_handler()
  - _kb_local_salvar/aprovar()    — knowledge base local
  - _ultimo_dia_com_dados()       — última data sincronizada
  - _verificar_sync_falhou()      — aviso de dados desatualizados
  - AJUDA_TEXTO                   — texto do /ajuda
"""
import logging
import os
import re
import time
from typing import Optional

from utils.formatters import fmt_brl, fmt_x, fmt_pct, fmt_n, roas_emoji

logger = logging.getLogger(__name__)

_HANDLER_TIMEOUT = int(os.getenv("SLACK_HANDLER_TIMEOUT", "60"))

_CLIENTES_NOTION: dict[str, str] = {
    "consorcio":         "consorcio_sorteado",
    "consórcio":         "consorcio_sorteado",
    "sorteado":          "consorcio_sorteado",
    "source":            "source_of",
    "source of":         "source_of",
    "máximo":            "maximo_servico",
    "maximo":            "maximo_servico",
    "serviço":           "maximo_servico",
    "mushin":            "be_mushin",
    "be mushin":         "be_mushin",
    "bazar":             "bazar_precatorio",
    "precatório":        "bazar_precatorio",
    "precatorio":        "bazar_precatorio",
}

AJUDA_TEXTO = """
*⚙️ Agente de Performance Guará — Source OF*

Sou seu analista de mídia paga. Me pergunte qualquer coisa sobre as campanhas da Source OF no Meta Ads — ou use os atalhos abaixo.

──────────────────────────────────────────────────
*💬 Como usar*

Mencione `@agente` em qualquer canal ou mande uma DM:
> _@agente como foi o mês de junho?_

Ou use os slash commands direto:
> `/hoje` · `/semana` · `/top` · `/alertas` · `/ajuda`

──────────────────────────────────────────────────
*🟢 10 Exemplos práticos*

*1.* _Qual campanha teve melhor ROAS essa semana?_
*2.* _Resumo completo de junho — investimento, receita e compras_
*3.* _Como foi a ABO nos últimos 30 dias?_
*4.* _Compare a Advantage vs ABO em maio_
*5.* _Qual campanha está com ROAS abaixo de 1?_ *(alertas)*
*6.* _Mostre o funil de conversão da ABO em junho_
*7.* _Qual o custo por compra médio esse mês?_
*8.* _Top 3 campanhas que geraram mais compras em 2026_
*9.* _Como evoluiu o investimento da Advantage dia a dia em junho?_
*10.* _Tem alguma campanha com alta frequência que pode estar saturando?_

──────────────────────────────────────────────────
*⚡ Slash Commands*

`/hoje` — resumo do dia
`/semana` — últimos 7 dias
`/mes` — mês atual · `/mes junho` — mês específico
`/top` — top 5 por ROAS · `/top compras` · `/top 10`
`/campanha ABO` — detalhe de uma campanha
`/campanhas` — lista todas as campanhas
`/compare ABO vs Advantage` — comparação lado a lado
`/funil` — funil de conversão · `/funil ABO` — de uma campanha
`/alertas` — campanhas com problema de ROAS ou conversão
`/relatorio` — relatório semanal (semana passada) no Notion · `/relatorio mes` · `/relatorio notion`
`/exemplos` — mais exemplos de perguntas
`/metricas` — lista de todas as métricas disponíveis
`/status` — status do sistema
`/ajuda` — este menu

──────────────────────────────────────────────────
*📅 Períodos que entendo*
`hoje` · `essa semana` · `semana passada` · `esse mês` · `mês passado`
`últimos 7/30/90 dias` · `em junho` · `em 2025` · `black friday`
`01/06/2026` · `segundo trimestre` · `janeiro a março`

*👍 Dê 👍 nas respostas que gostar — entram para a memória do agente!*
"""

def _detectar_formato_export(text: str) -> tuple[str | None, str | None]:
    """
    Detecta se o usuário quer exportar dados e para qual formato.
    Retorna (formato_export, cliente_notion).
    
    Formatos:
      'excel'        — gera planilha .xlsx
      'pdf'          — gera PDF
      'notion'       — publica página no Notion
      'excel+notion' — envia o .xlsx E publica no Notion
    """
    tem_excel  = bool(re.search(r"\bexcel\b|\bplanilha\b|\bxlsx\b", text, re.I))
    tem_pdf    = bool(re.search(r"\bpdf\b", text, re.I))
    tem_notion = bool(re.search(r"\bnotion\b|\bpubli[cq]|\bpublic[ae]\.?\b|salvar no notion|enviar (para|pro) notion", text, re.I))

    formato = None
    if tem_excel and tem_notion:
        formato = "excel+notion"
    elif tem_notion:
        formato = "notion"
    elif tem_excel:
        formato = "excel"
    elif tem_pdf:
        formato = "pdf"

    # Detecta cliente do Notion no texto usando o dict de módulo
    cliente = None
    t = text.lower()
    for token, key in _CLIENTES_NOTION.items():
        if token in t:
            cliente = key
            break

    return formato, cliente


def _brl(v) -> str:
    """Formata valor como BRL."""
    if v is None:
        return "—"
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_resumo_relatorio(resultado: dict) -> str:
    """
    Formata resumo de relatório diário/semanal/mensal no padrão visual:
      tabelas ASCII com separadores, seções por tema, emojis.
    Usado nos crons de relatório e no fluxo multi-plataforma.
    """
    r          = resultado.get("resumo") or {}
    plataforma = resultado.get("plataforma", "")
    data_str   = resultado.get("data") or resultado.get("data_fim") or ""
    campanhas  = resultado.get("campanhas") or []
    anterior   = resultado.get("anterior") or {}
    shopify    = r.get("shopify") or resultado.get("shopify") or {}
    url        = resultado.get("url") or ""

    plat_labels = {"meta_ads": "Meta Ads", "google_ads": "Google Ads", "shopify": "Shopify"}
    plat_nome   = plat_labels.get(plataforma, plataforma.replace("_", " ").title())

    # Formatos
    def brl(v):
        if v is None: return "—"
        try: return f"R$ {float(v):,.2f}".replace(",","X").replace(".",",").replace("X",".")
        except: return "—"
    def pct(v):
        if v is None: return "—"
        try: return f"{float(v):.2f}%"
        except: return "—"
    def num(v, dec=0):
        if v is None: return "—"
        try:
            v = float(v)
            if dec: return f"{v:,.{dec}f}".replace(",",".")
            return f"{int(v):,}".replace(",",".")
        except: return "—"
    def roas_str(v):
        if v is None: return "—"
        try: return f"{float(v):.2f}x"
        except: return "—"
    # roas_emoji importado de utils.formatters

    parts = []

    # ── Shopify standalone ───────────────────────────────────────────────
    if plataforma == "shopify":
        sh = r.get("shopify") or r
        sep = "─" * 33
        rows = [
            ("📦 Pedidos",          num(sh.get("pedidos"))),
            ("💰 Receita",          brl(sh.get("receita"))),
            ("🆕 Novos Clientes",   num(sh.get("novos_clientes"))),
            ("🔁 Recompradores",    num(sh.get("recompradores"))),
            ("🏷️ Ticket Médio",    brl(sh.get("ticket_medio"))),
            ("🎟️ Total Desconto",  brl(sh.get("total_desconto"))),
            ("🏅 Pedidos >R$300",   num(sh.get("pedidos_acima_300"))),
        ]
        w = max(len(k) for k,_ in rows)
        tbl = f"{sep}\nMétrica{' '*(w-7)} │ Valor\n{sep}\n"
        tbl += "\n".join(f"{k}{' '*(w-len(k))} │ {v}" for k,v in rows)
        tbl += f"\n{sep}"
        parts.append(f"🛒 *Shopify — Dados da Loja*\n```\n{tbl}\n```")
        if url:
            parts.append(f"📎 <{url}|Abrir no Notion>")
        return "\n".join(parts)

    # ── Visão Geral ──────────────────────────────────────────────────────
    invest    = r.get("investimento")
    receita   = r.get("receita")
    roas_v    = r.get("roas")
    convs     = r.get("conversoes")
    camps_n   = r.get("campanhas")
    alcance   = r.get("alcance")
    impressoes= r.get("impressoes")
    cliques   = r.get("cliques")
    ctr_v     = r.get("ctr")
    cpc_v     = r.get("cpc")
    cpm_v     = r.get("cpm")

    sep34 = "─" * 34
    sep42 = "─" * 42
    sep54 = "─" * 54

    # Resumo executivo
    rows_resumo = [
        (f"🏆 ROAS",        roas_str(roas_v)),
        (f"💰 Investimento", brl(invest)),
        (f"📈 Receita",      brl(receita)),
        (f"✅ Conversões",   num(convs)),
        (f"📊 Campanhas",    num(camps_n)),
    ]
    w = max(len(k) for k,_ in rows_resumo)
    tbl_res = f"{sep34}\nMétrica{' '*(w-7)} │ Valor\n{sep34}\n"
    tbl_res += "\n".join(f"{k}{' '*(w-len(k))} │ {v}" for k,v in rows_resumo)
    tbl_res += f"\n{sep34}"
    parts.append(f"```\n{tbl_res}\n```")

    # Visão Geral
    rows_vg = [
        ("💰 Investimento",  brl(invest)),
        ("📈 Receita",       brl(receita)),
        ("🏆 ROAS",          roas_str(roas_v)),
        ("✅ Conversões",    num(convs)),
        ("📊 Campanhas Ativas", num(camps_n)),
        ("👥 Alcance",       num(alcance) if alcance else "—"),
    ]
    w = max(len(k) for k,_ in rows_vg)
    tbl_vg = f"{sep42}\nMétrica{' '*(w-7)} │ Valor\n{sep42}\n"
    tbl_vg += "\n".join(f"{k}{' '*(w-len(k))} │ {v}" for k,v in rows_vg)
    tbl_vg += f"\n{sep42}"
    parts.append(f":bar_chart: *Visão Geral*\n```\n{tbl_vg}\n```")

    # Funil
    if impressoes or cliques or convs:
        imp = float(impressoes or 0)
        cli = float(cliques or 0)
        con = float(convs or 0)
        ctr_funil  = f"{(cli/imp*100):.2f}% CTR"    if imp  else "—"
        conv_funil = f"{(con/cli*100):.2f}% conv/clique" if cli else "—"
        sep_funil = "─" * 46
        rows_funil = [
            ("🖥️ Impressões",  num(imp),        "100%"),
            ("🔗 Cliques",     num(cli),         ctr_funil),
            ("✅ Conversões",  num(con),         conv_funil),
        ]
        w0 = max(len(r[0]) for r in rows_funil)
        w1 = max(len(r[1]) for r in rows_funil)
        tbl_funil  = f"{sep_funil}\n{'Etapa':{w0}} │ {'Volume':{w1}} │ Taxa\n{sep_funil}\n"
        tbl_funil += "\n".join(f"{r[0]:{w0}} │ {r[1]:{w1}} │ {r[2]}" for r in rows_funil)
        tbl_funil += f"\n{sep_funil}"
        parts.append(f":arrow_down_small: *Funil de Conversão*\n```\n{tbl_funil}\n```")

    # Métricas de custo
    if ctr_v or cpc_v or cpm_v:
        sep18 = "─" * 22
        rows_custo = [
            ("🎯 CTR", pct(ctr_v)),
            ("💸 CPC", brl(cpc_v)),
            ("📣 CPM", brl(cpm_v)),
        ]
        w = max(len(k) for k,_ in rows_custo)
        tbl_custo = f"{sep18}\nMétrica{' '*(w-7)} │ Valor\n{sep18}\n"
        tbl_custo += "\n".join(f"{k}{' '*(w-len(k))} │ {v}" for k,v in rows_custo)
        tbl_custo += f"\n{sep18}"
        parts.append(f":triangular_ruler: *Métricas de Custo*\n```\n{tbl_custo}\n```")

    # Comparativo dia anterior
    if anterior and anterior.get("investimento"):
        def var_str(atual, ant, maior_melhor=True):
            try:
                a, b = float(atual or 0), float(ant or 0)
                if b == 0: return "—"
                pct_var = (a - b) / b * 100
                arrow = "↑" if a > b else "↓"
                ok = (a > b) == maior_melhor
                icon = "✅" if ok else "⚠️"
                return f"{icon} {arrow} {abs(pct_var):.1f}%"
            except: return "—"

        rows_comp = [
            ("💰 Investimento", brl(anterior.get("investimento")), brl(invest),       var_str(invest, anterior.get("investimento"), False)),
            ("📈 Receita",      brl(anterior.get("receita")),      brl(receita),      var_str(receita, anterior.get("receita"))),
            ("ROAS",            roas_str(anterior.get("roas")),    roas_str(roas_v),  var_str(roas_v, anterior.get("roas"))),
            ("✅ Conversões",   num(anterior.get("conversoes")),   num(convs),        var_str(convs, anterior.get("conversoes"))),
            ("🎯 CTR",          pct(anterior.get("ctr")),          pct(ctr_v),        var_str(ctr_v, anterior.get("ctr"))),
        ]
        w0 = max(len(r[0]) for r in rows_comp)
        w1 = max(len(r[1]) for r in rows_comp)
        w2 = max(len(r[2]) for r in rows_comp)
        sep_comp = "─" * (w0+w1+w2+14)
        tbl_comp  = f"{sep_comp}\n{'Métrica':{w0}} │ {'Ontem':{w1}} │ {'Hoje':{w2}} │ Var.\n{sep_comp}\n"
        tbl_comp += "\n".join(f"{r[0]:{w0}} │ {r[1]:{w1}} │ {r[2]:{w2}} │ {r[3]}" for r in rows_comp)
        tbl_comp += f"\n{sep_comp}"
        parts.append(f":chart_with_upwards_trend: *Comparativo — Dia Anterior*\n```\n{tbl_comp}\n```")

    # Tabela de campanhas removida — só alertas de campanhas sem conversão
    if campanhas:
        alertas_camp = []
        for c in campanhas:
            if (c.get("conversoes") or 0) == 0 and (c.get("investimento") or 0) > 20:
                nome = (c.get("campanha") or "—")[:34]
                alertas_camp.append((nome, brl(c.get("investimento")), "Sem conversão"))
        if alertas_camp:
            w0 = max(len(r[0]) for r in alertas_camp)
            w1 = max(len(r[1]) for r in alertas_camp)
            sep_a = "─" * (w0+w1+len(alertas_camp[0][2])+8)
            tbl_a  = f"{sep_a}\nCampanha{' '*(w0-8)} │ {'Invest.':{w1}} │ Motivo\n{sep_a}\n"
            tbl_a += "\n".join(f"{r[0]:{w0}} │ {r[1]:{w1}} │ {r[2]}" for r in alertas_camp)
            tbl_a += f"\n{sep_a}"
            parts.append(f":warning: *Alertas — Campanhas para Revisar*\n```\n{tbl_a}\n```")
    # Shopify embutido (source_of google_ads/meta_ads inclui shopify no resultado)
    sh = resultado.get("shopify") or r.get("shopify")
    if sh and sh.get("receita") and plataforma != "shopify":
        sep_sh = "─" * 33
        rows_sh = [
            ("📦 Pedidos",         num(sh.get("pedidos"))),
            ("💰 Receita",         brl(sh.get("receita"))),
            ("🆕 Novos Clientes",  num(sh.get("novos_clientes"))),
            ("🔁 Recompradores",   num(sh.get("recompradores"))),
            ("🏷️ Ticket Médio",   brl(sh.get("ticket_medio"))),
            ("🎟️ Total Desconto", brl(sh.get("total_desconto"))),
            ("🏅 Pedidos >R$300",  num(sh.get("pedidos_acima_300"))),
        ]
        w = max(len(k) for k,_ in rows_sh)
        tbl_sh  = f"{sep_sh}\nMétrica{' '*(w-7)} │ Valor\n{sep_sh}\n"
        tbl_sh += "\n".join(f"{k}{' '*(w-len(k))} │ {v}" for k,v in rows_sh)
        tbl_sh += f"\n{sep_sh}"
        parts.append(f":shopping_trolley: *Shopify — Dados da Loja*\n```\n{tbl_sh}\n```")

    # Rodapé com data e link
    data_fmt = f"{data_str[8:10]}/{data_str[5:7]}/{data_str[:4]}" if len(data_str) == 10 else data_str
    footer_parts = []
    if data_fmt and data_fmt != "//":
        footer_parts.append(data_fmt)
    cliente_key = resultado.get("cliente_key", "")
    cliente_labels = {"source_of": "Source OF", "pod_kombucha": "POD Kombucha"}
    if cliente_key:
        footer_parts.append(cliente_labels.get(cliente_key, cliente_key))
    if plat_nome:
        footer_parts.append(plat_nome)
    if campanhas:
        footer_parts.append(f"{len(campanhas)} campanhas ativas")

    if footer_parts:
        parts.append(f"_{'  ·  '.join(footer_parts)}_")

    if url:
        parts.append(f"📎 <{url}|Abrir no Notion>")

    return "\n".join(parts)



# ── MELHORIA 7: Sugestões pro-ativas por intent ─────────────────────────────

from typing import Optional

_SUGESTOES_POR_INTENT: dict = {
    "resumo_geral":            "💡 *Próximos passos:* `/top roas` · `/compare` · `/alertas`",
    "top_roas":                "💡 *Também pode:* `/top compras` · `/campanha <nome>` · `/funil`",
    "top_compras":             "💡 *Também pode:* `/top roas` · `/funil` · `/compare`",
    "top_receita":             "💡 *Também pode:* `/top roas` · `/top compras`",
    "performance_campanha":    "💡 *Aprofunde:* `/funil <campanha>` · `/compare <campanha> vs Advantage`",
    "alertas_roas":            "💡 *Também veja:* `/sem_conversao` · `/top roas` · `/funil`",
    "sem_conversao":           "💡 *Também pode:* `/alertas` · `/top roas`",
    "funil":                   "💡 *Aprofunde:* `/funil` detalhado · `/top compras`",
    "funil_detalhado":         "💡 *Também pode:* `/top roas` · `/alertas`",
    "comparar":                "💡 *Também pode:* comparar períodos · `/top roas`",
    "comparar_periodos":       "💡 *Também pode:* `/top roas` · `/resumo_geral`",
    "comparar_wow":            "💡 *Também pode:* comparar mês a mês · `/top roas`",
    "comparar_mom":            "💡 *Também pode:* histórico mensal · `/top roas`",
    "historico_mensal":        "💡 *Também pode:* comparar mom · `/resumo_geral`",
    "periodo":                 "💡 *Também pode:* `/top roas` · `/funil` · `/alertas`",
    "saturacao_frequencia":    "💡 *Considera:* pausar campanhas com frequência > 4. Use `/alertas`.",
    "eficiencia_criativo":     "💡 *Aprofunde:* métricas de vídeo · `/top ctr`",
    "distribuicao_investimento": "💡 *Também pode:* `/top roas` · `/resumo_geral`",
    "resumo_executivo":        "💡 *Aprofunde:* `/alertas` · `/top roas` · `/funil`",
    "evolucao_diaria":         "💡 *Também pode:* comparar wow · `/top roas`",
    "metricas_video":          "💡 *Também pode:* eficiência criativo · `/top ctr`",
    "metricas_engajamento":    "💡 *Também pode:* `/top ctr` · eficiência criativo",
    "ranking_metrica":         "💡 *Também pode:* `/top roas` · `/top compras`",
}


def _sugestao_proativa(intent: str) -> Optional[str]:
    """Retorna sugestão de follow-up ou None se não houver mapeamento."""
    return _SUGESTOES_POR_INTENT.get(intent)


# ── MELHORIA 8: Alerta de cobertura de dados ─────────────────────────────────

def _verificar_cobertura_dados_handler(
    data_inicio,
    data_fim,
    cliente_key,
    plataforma,
) -> Optional[str]:
    """Wrapper que chama nlp._verificar_cobertura_dados sem import circular."""
    try:
        from agent.nlp import _verificar_cobertura_dados
        return _verificar_cobertura_dados(data_inicio, data_fim, cliente_key, plataforma)
    except Exception as e:
        logger.debug("[handler] _verificar_cobertura_dados: %s", e)
        return None


# ── MELHORIA 9: Base de Conhecimento local (SQLite) ───────────────────────────

def _kb_local_salvar(pergunta: str, resposta: str, intent: str, msg_ts=None) -> None:
    """
    Salva resposta na tabela local kb_cache em campanhas.db.
    Não aprovada até receber 👍.
    """
    try:
        import sqlite3 as _sq
        from pathlib import Path as _P
        _db = _P(__file__).parent.parent / "data" / "campanhas.db"
        conn = _sq.connect(str(_db), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kb_cache (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                pergunta    TEXT NOT NULL,
                resposta    TEXT NOT NULL,
                intent      TEXT,
                aprovado    INTEGER DEFAULT 0,
                slack_msg_ts TEXT,
                criado_em   TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_kb_cache_ts ON kb_cache(slack_msg_ts);
        """)
        conn.execute("""
            INSERT INTO kb_cache (pergunta, resposta, intent, slack_msg_ts)
            VALUES (?, ?, ?, ?)
        """, (pergunta, resposta, intent, msg_ts))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[KB local] salvar: %s", e)


def _kb_local_aprovar(slack_msg_ts: str) -> bool:
    """Marca a entrada como aprovada na tabela kb_cache local."""
    try:
        import sqlite3 as _sq
        from pathlib import Path as _P
        _db = _P(__file__).parent.parent / "data" / "campanhas.db"
        conn = _sq.connect(str(_db), check_same_thread=False)
        cur  = conn.execute(
            "UPDATE kb_cache SET aprovado = 1 WHERE slack_msg_ts = ?",
            (slack_msg_ts,)
        )
        conn.commit()
        ok = cur.rowcount > 0
        conn.close()
        return ok
    except Exception as e:
        logger.debug("[KB local] aprovar: %s", e)
        return False


# ── MELHORIA 10: Fallback quando sync falhou ─────────────────────────────────

def _ultimo_dia_com_dados(cliente_key, plataforma):
    """
    Retorna a última data com dados disponíveis no banco para o cliente/plataforma.
    Retorna date ou None.
    """
    try:
        import sqlite3 as _sq
        from datetime import date as _date
        from pathlib import Path as _P
        _db = _P(__file__).parent.parent / "data" / "campanhas.db"
        conn = _sq.connect(str(_db), check_same_thread=False)
        conn.row_factory = _sq.Row

        if cliente_key == "pod_kombucha" and plataforma == "meta_ads":
            tabela, col = "pod_kombucha_meta_ads_diario", "data"
        elif cliente_key == "pod_kombucha":  # google_ads ou default
            tabela, col = "pod_kombucha_gads_diario", "data"
        else:
            tabela, col = "campanhas_unificada", "data_inicio"

        # verifica se a coluna existe
        info = conn.execute(f"PRAGMA table_info({tabela})").fetchall()
        colunas = [r["name"] for r in info]
        if col not in colunas:
            col = "data" if "data" in colunas else (colunas[0] if colunas else "data")

        row = conn.execute(f"SELECT MAX({col}) AS ultimo FROM {tabela}").fetchone()
        conn.close()
        if row and row["ultimo"]:
            return _date.fromisoformat(str(row["ultimo"])[:10])
    except Exception as e:
        logger.debug("[handler] _ultimo_dia_com_dados: %s", e)
    return None


def _verificar_sync_falhou(cliente_key, plataforma) -> Optional[str]:
    """
    MELHORIA 10: Verifica se o sync recente falhou para o cliente/plataforma.
    Se cron_runs mostra falha recente, retorna aviso com último dia disponível.
    Retorna None se tudo ok.
    """
    try:
        from datetime import date as _date, timedelta as _td
        from db.cron_log import listar_runs

        hoje = _date.today()
        d3   = hoje - _td(days=3)

        ck   = cliente_key  # None = sem filtro de cliente (retorna todos)
        runs = listar_runs(cliente_key=ck, limit=5)
        falhou = any(
            r["status"] != "sucesso" and r.get("criado_em", "") >= d3.isoformat()
            for r in runs
        )
        if not falhou:
            return None

        ultimo = _ultimo_dia_com_dados(cliente_key, plataforma)
        if ultimo is None:
            return (
                "⚠️ *Sincronização recente falhou.* "
                "Os dados podem estar desatualizados. Tente novamente mais tarde."
            )

        return (
            f"⚠️ *Sincronização recente falhou.* "
            f"Os dados mais recentes disponíveis são de *{ultimo.strftime('%d/%m/%Y')}*. "
            f"Consultas para períodos mais recentes podem estar incompletas."
        )
    except Exception as e:
        logger.debug("[handler] _verificar_sync_falhou: %s", e)
        return None



