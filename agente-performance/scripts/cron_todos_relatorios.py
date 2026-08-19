"""
scripts/cron_todos_relatorios.py

Dispara relatórios para TODOS os clientes ativos lidos do registry (clientes.json).
Adicionar um novo cliente = só editar clientes.json. Sem tocar neste script.

Cada cliente tem seu próprio bloco de erro isolado — falha de um não
impede o outro.

Funcionalidades:
  - Item 9:  Comparativo delta (ROAS, invest) na mensagem âncora
  - Item 10: Alertas proativos (ROAS < threshold) disparados antes do relatório completo
  - Item 11: Nome/emoji do cliente vem do registry (clientes.json)

Uso:
    python scripts/cron_todos_relatorios.py --periodo semana
    python scripts/cron_todos_relatorios.py --periodo mes
    python scripts/cron_todos_relatorios.py --periodo diario
    python scripts/cron_todos_relatorios.py --cliente source_of --periodo semana
    python scripts/cron_todos_relatorios.py --apenas-alertas   # só checa alertas, sem relatório completo
"""

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Thresholds de alerta (item 10) ────────────────────────────────────────────
ALERTA_ROAS_MIN      = float(os.getenv("ALERTA_ROAS_MIN", "2.0"))      # Source OF
ALERTA_ROAS_MIN_GADS = float(os.getenv("ALERTA_ROAS_MIN_GADS", "1.5")) # Google Ads



# ── Formatadores (importados de utils.formatters) ────────────────────────────
from utils.formatters import fmt_brl as _fmt_brl, fmt_x as _fmt_x, fmt_n as _fmt_n, fmt_pct  # noqa: F401


def _delta_str(atual, anterior, maior_melhor=True) -> str:
    """Retorna string de variação: ↑ +12% ✅ ou ↓ -8% ⚠️"""
    try:
        a, b = float(atual), float(anterior)
        if b == 0: return ""
        pct = (a - b) / abs(b) * 100
        if abs(pct) < 1: return f"(→ {pct:+.1f}%)"
        up   = pct > 0
        seta = "↑" if up else "↓"
        ok   = up == maior_melhor
        emoji = "✅" if ok else "⚠️"
        return f"({seta} {abs(pct):.0f}% {emoji})"
    except: return ""


# ── Período ───────────────────────────────────────────────────────────────────

def resolver_periodo(periodo: str):
    hoje = date.today()
    if periodo == "diario":
        ontem = hoje - timedelta(days=1)
        return ontem, ontem
    elif periodo == "semana":
        seg = hoje - timedelta(days=hoje.weekday() + 7)
        return seg, seg + timedelta(days=6)
    elif periodo == "mes":
        primeiro_este = hoje.replace(day=1)
        ultimo_mes = primeiro_este - timedelta(days=1)
        return ultimo_mes.replace(day=1), ultimo_mes
    raise ValueError(f"Período inválido: {periodo}")


def periodo_anterior(data_inicio: date, data_fim: date):
    """Retorna o período imediatamente anterior com a mesma duração."""
    duracao = (data_fim - data_inicio).days + 1
    ant_fim = data_inicio - timedelta(days=1)
    ant_ini = ant_fim - timedelta(days=duracao - 1)
    return ant_ini, ant_fim


# ── Slack ─────────────────────────────────────────────────────────────────────

def postar_slack_fallback(msg: str, max_tentativas: int = 3) -> bool:
    """Fallback via Bot API (com canal correto) ou webhook se token indisponível."""
    import urllib.request, json, time, requests as _req
    # Tenta primeiro via Bot API com o canal configurado
    token = os.getenv("SLACK_BOT_TOKEN", "")
    ch = os.getenv("SLACK_CHANNEL", "performance").lstrip("#")
    _ok = False
    _erro = None
    _slack_ts = None
    if token:
        try:
            r = _req.post("https://slack.com/api/chat.postMessage",
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                          json={"channel": ch, "text": msg, "mrkdwn": True}, timeout=15)
            _rj = r.json()
            if _rj.get("ok"):
                _ok = True
                _slack_ts = _rj.get("ts")
            else:
                _erro = _rj.get("error")
                logger.warning("Bot API fallback falhou: %s", _erro)
        except Exception as e:
            _erro = str(e)
            logger.warning("Bot API fallback erro: %s", e)
    # Último recurso: webhook (canal fixo)
    if not _ok:
        webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
        if not webhook_url:
            _ok = False
        else:
            payload = json.dumps({"text": msg}).encode()
            for i in range(1, max_tentativas + 1):
                try:
                    req = urllib.request.Request(webhook_url, data=payload,
                                                 headers={"Content-Type": "application/json"}, method="POST")
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        if resp.status == 200:
                            _ok = True; break
                except Exception as e:
                    _erro = str(e)
                    logger.warning("Slack webhook (tentativa %d): %s", i, e)
                    if i < max_tentativas: time.sleep(2 ** i)
    # ── Registra no log ───────────────────────────────────────────
    try:
        from db.slack_log import registrar_disparo
        _tipo_fb = "erro" if "❌" in msg or "FALHOU" in msg else "fallback"
        registrar_disparo(
            endpoint="chat.postMessage/fallback",
            channel=ch,
            tipo=_tipo_fb,
            texto_preview=msg[:200],
            slack_ts=_slack_ts,
            ok=_ok,
            erro_msg=_erro if not _ok else None,
        )
    except Exception as _le:
        logger.debug("[SlackLog] Erro no log (non-critical): %s", _le)
    return _ok


def _postar_slack_api(text: str, channel: str = None) -> bool:
    """Posta mensagem simples via Bot API."""
    import requests as _req
    token = os.getenv("SLACK_BOT_TOKEN", "")
    ch = (channel or os.getenv("SLACK_CHANNEL", "performance")).lstrip("#")
    if not token: return postar_slack_fallback(text)
    r = _req.post("https://slack.com/api/chat.postMessage",
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                  json={"channel": ch, "text": text, "mrkdwn": True}, timeout=15)
    return r.json().get("ok", False)


# ── Delta (item 9) ────────────────────────────────────────────────────────────

def _calcular_delta(resultado: dict, data_inicio: date, data_fim: date, cliente_key: str) -> dict:
    """
    Calcula variação vs período anterior para exibir na âncora.
    Retorna dict: {métrica: (valor_atual, valor_anterior, maior_melhor)}
    """
    try:
        ant_ini, ant_fim = periodo_anterior(data_inicio, data_fim)
        r = resultado.get("resumo", {})

        # Determina plataforma e tabela
        from db.registry import get_cliente
        cfg      = get_cliente(cliente_key)
        tabela   = cfg.get("tabela_db", "campanhas")
        is_gads  = cfg.get("plataforma") == "google_ads"

        from db.connections import query_fed
        plataforma = None
        for plat, pcfg in cfg.get("plataformas", {}).items():
            if pcfg.get("ativo", False) and plat != "shopify":
                plataforma = plat
                break

        rows = query_fed("""
            SELECT
                SUM(investimento) AS invest,
                CASE WHEN SUM(investimento) > 0
                     THEN SUM(valor_compras) / SUM(investimento) END AS roas
            FROM campanhas_unificada
            WHERE cliente_key = ? AND data_inicio BETWEEN ? AND ?
        """, (cliente_key, str(ant_ini), str(ant_fim)))
        row = rows[0] if rows else None
        if not row or not row["invest"]:
            return {}

        delta = {}
        roas_atual = r.get("roas")
        roas_ant   = row["roas"]
        if roas_atual and roas_ant:
            delta["ROAS"] = (roas_atual, roas_ant, True)

        invest_atual = r.get("invest") or r.get("total_invest")
        invest_ant   = row["invest"]
        if invest_atual and invest_ant:
            delta["Invest."] = (invest_atual, invest_ant, None)  # neutro

        return delta

    except Exception as e:
        logger.warning("[Delta] Erro ao calcular delta: %s", e)
        return {}


# ── Alertas proativos (item 10) ───────────────────────────────────────────────

def verificar_alertas(cliente_key: str, data_inicio: date, data_fim: date) -> list[str]:
    """
    Checa se há campanhas abaixo do threshold de ROAS.
    Retorna lista de mensagens de alerta (vazia = tudo OK).
    """
    alertas = []
    try:
        from db.registry import get_cliente
        cfg       = get_cliente(cliente_key)
        nome      = cfg.get("nome", cliente_key)
        emoji     = cfg.get("slack_emoji", "📊")
        plats_cfg = cfg.get("plataformas", {})
        is_gads   = plats_cfg.get("google_ads", {}).get("ativo", False) and not plats_cfg.get("meta_ads", {}).get("ativo", False)
        threshold = ALERTA_ROAS_MIN_GADS if is_gads else ALERTA_ROAS_MIN

        from db.connections import query_fed
        rows = query_fed("""
            SELECT campanha,
                   SUM(investimento) AS invest,
                   SUM(receita)      AS receita,
                   CASE WHEN SUM(investimento) > 0
                        THEN SUM(receita) / SUM(investimento) END AS roas
            FROM campanhas_unificada
            WHERE cliente_key = ?
              AND data_inicio BETWEEN ? AND ?
              AND investimento > 0
            GROUP BY campanha
            HAVING roas < ? OR roas IS NULL
            ORDER BY invest DESC
            LIMIT 5
        """, (cliente_key, str(data_inicio), str(data_fim), threshold))

        if rows:
            alertas.append({
                "cliente_key": cliente_key,
                "nome":        nome,
                "emoji":       emoji,
                "threshold":   threshold,
                "periodo":     f"{data_inicio.strftime('%d/%m')} a {data_fim.strftime('%d/%m/%Y')}",
                "campanhas":   [dict(r) for r in rows],
            })

    except Exception as e:
        logger.warning("[Alertas] Erro ao verificar alertas para %s: %s", cliente_key, e)

    return alertas


def postar_alerta_ancorado(alerta: dict, channel: str = None) -> bool:
    """
    Posta alerta de ROAS com estrutura âncora + thread:
      - Âncora: ⚠️ Alerta ROAS · Source OF · 13/07 → 19/07
      - Thread: tabela com campanhas problemáticas detalhada
    """
    import requests as _req
    from slack.poster import _post_api, _channel, _tabela_txt, _bloco_header, _bloco_section, _bloco_divider, _bloco_context, _chunks
    import time

    ch       = (channel or _channel()).lstrip("#")
    nome     = alerta["nome"]
    emoji    = alerta["emoji"]
    periodo  = alerta["periodo"]
    thresh   = alerta["threshold"]
    camps    = alerta["campanhas"]

    # ── 1. Âncora ──────────────────────────────────────────────────────────
    ancora = f"⚠️ *Alerta de ROAS · {nome}*\n_{periodo} · {len(camps)} campanha(s) abaixo de {thresh}x_"
    resp = _post_api("chat.postMessage", {"channel": ch, "text": ancora, "mrkdwn": True})
    if not resp.get("ok"):
        logger.error("[Alerta] Falha ao postar âncora: %s", resp.get("error"))
        return False

    thread_ts = resp["ts"]
    logger.info("[Alerta] Âncora postada: ts=%s", thread_ts)

    # ── 2. Detalhes na thread ───────────────────────────────────────────────
    rows_tabela = []
    for c in camps:
        roas_str = f"{c['roas']:.2f}x" if c.get("roas") else "—"
        rows_tabela.append([
            c["campanha"][:35],
            _fmt_brl(c.get("invest")),
            _fmt_brl(c.get("receita")),
            roas_str,
        ])

    blocks = [
        _bloco_header(f"⚠️ Alerta ROAS — {nome} · {periodo}"),
        _bloco_divider(),
        _bloco_section(f"*Campanhas com ROAS abaixo de {thresh}x:*"),
        _bloco_section(_tabela_txt(
            ["Campanha", "Invest.", "Receita", "ROAS"],
            rows_tabela,
        )),
        _bloco_divider(),
        _bloco_context(
            f"_Threshold configurado: {thresh}x · "
            f"Verifique criativos, segmentação e orçamento das campanhas acima._"
        ),
    ]

    payload = {
        "channel":   ch,
        "thread_ts": thread_ts,
        "blocks":    blocks,
        "text":      f"Detalhes do alerta ROAS — {nome}",
    }
    resp2 = _post_api("chat.postMessage", payload)
    if not resp2.get("ok"):
        logger.error("[Alerta] Falha ao postar thread: %s", resp2.get("error"))
    else:
        logger.info("[Alerta] Thread detalhada postada ts=%s", thread_ts)

    return True


# ── Pipeline por cliente ──────────────────────────────────────────────────────

def executar_cliente(
    cliente_key: str,
    cfg: dict,
    periodo: str,
    data_inicio: date,
    data_fim: date,
    periodo_str: str,
    tipo_label: str,
    tipo_builder: str,
    publicar: bool,
    apenas_alertas: bool = False,
) -> dict:
    """
    Executa loader + (alertas proativos) + builder + delta + Slack para um cliente.
    Retorna dict com resultado ou erro.
    """
    from db.cron_log import registrar_run

    nome  = cfg.get("nome", cliente_key)
    emoji = cfg.get("slack_emoji", "📊")
    label = cfg.get("slack_label", nome)

    logger.info("--- [%s] %s ---", cliente_key, label)

    # ── Item 10: Alertas proativos ────────────────────────────────────────
    alertas = verificar_alertas(cliente_key, data_inicio, data_fim)
    for alerta in alertas:
        # ── Deduplicação: não reenvia alerta já postado hoje ──────────────
        from db.relatorio_log import alerta_ja_enviado_hoje, registrar_alerta
        _data_ref = str(data_fim)
        if alerta_ja_enviado_hoje(cliente_key, _data_ref):
            logger.info("[%s] Alerta ROAS já enviado hoje (%s) — ignorando duplicata", cliente_key, _data_ref)
            continue
        ok = postar_alerta_ancorado(alerta)  # âncora + thread detalhada
        logger.info("[%s] Alerta postado (ancorado): %s", cliente_key, "OK" if ok else "FALHOU")
        if ok:
            registrar_alerta(
                cliente_key=cliente_key,
                data_ref=_data_ref,
                campanhas_n=len(alerta.get("campanhas", [])),
                slack_ok=True,
            )

    if apenas_alertas:
        if not alertas:
            logger.info("[%s] Sem alertas.", cliente_key)
        return {"ok": True, "alertas": len(alertas)}

    run_id = registrar_run(
        cliente_key=cliente_key,
        periodo=periodo,
        data_inicio=str(data_inicio),
        data_fim=str(data_fim),
        status="iniciado",
    )

    try:
        # ── Loader (com retry) ────────────────────────────────────────────
        from db.registry import get_loader, get_builder
        import time as _time
        loader_fn = get_loader(cliente_key)
        _loader_ok = False
        for _attempt in range(1, 4):  # até 3 tentativas
            try:
                loader_fn()
                # sync via API já realizado pelos crons sync_*_diario.py
                _loader_ok = True
                break
            except Exception as e:
                logger.warning("[%s] Loader tentativa %d/3 falhou: %s", cliente_key, _attempt, e)
                if _attempt < 3:
                    _time.sleep(10)  # aguarda 10s antes de retry
        if not _loader_ok:
            logger.error("[%s] Loader falhou após 3 tentativas — abortando relatório", cliente_key)
            raise RuntimeError(f"Sync de dados falhou após 3 tentativas para {cliente_key}")

        # ── Builder ───────────────────────────────────────────────────────
        builder_fn = get_builder(cliente_key, tipo_builder)
        _t_inicio  = _time.monotonic()
        # Passa cliente_key/cliente apenas se o builder aceita (nem todos aceitam)
        import inspect as _inspect
        _builder_params = _inspect.signature(builder_fn).parameters
        _builder_kwargs: dict = {"data_inicio": data_inicio, "data_fim": data_fim, "publicar": publicar}
        if "cliente_key" in _builder_params:
            _builder_kwargs["cliente_key"] = cliente_key
        elif "cliente" in _builder_params:
            _builder_kwargs["cliente"] = cliente_key
        resultado  = builder_fn(**_builder_kwargs)
        _duracao_ms = int((_time.monotonic() - _t_inicio) * 1000)

        if resultado.get("erro"):
            raise RuntimeError(resultado["erro"])

        # ── Validação obrigatória antes do envio ao Slack ─────────────────
        _resumo_val  = resultado.get("resumo") or {}
        _url_val     = resultado.get("url", "—")
        _titulo_val  = resultado.get("titulo", "")
        _blocos_val  = resultado.get("blocos", [])
        _periodo_ok  = resultado.get("data") or resultado.get("data_inicio")

        _falhas_val: list[str] = []
        if not _resumo_val:
            _falhas_val.append("resumo ausente ou vazio")
        if not _resumo_val.get("investimento"):
            _falhas_val.append("investimento zerado/ausente no resumo")
        if not _titulo_val:
            _falhas_val.append("título do relatório ausente")
        if not _blocos_val:
            _falhas_val.append("nenhum bloco de conteúdo gerado")
        if not _periodo_ok:
            _falhas_val.append("período do relatório não identificado")
        if publicar and (_url_val == "—" or not _url_val):
            _falhas_val.append("URL do Notion ausente (publicação falhou?)")

        if _falhas_val:
            _motivo_val = "; ".join(_falhas_val)
            logger.error(
                "[%s] ❌ VALIDAÇÃO PRÉ-SLACK FALHOU — bloqueando envio. Motivos: %s",
                cliente_key, _motivo_val,
            )
            raise RuntimeError(
                f"Relatório bloqueado: dados inválidos ou incompletos ({_motivo_val})"
            )

        logger.info(
            "[%s] ✅ Validação pré-Slack OK — resumo=%s, título=%s, blocos=%d, url=%s, período=%s",
            cliente_key,
            bool(_resumo_val),
            bool(_titulo_val),
            len(_blocos_val),
            _url_val[:60] if _url_val != "—" else "—",
            _periodo_ok,
        )

        # ── Item 9: Delta vs período anterior ─────────────────────────────
        delta = _calcular_delta(resultado, data_inicio, data_fim, cliente_key)

        # ── Slack âncora + thread ─────────────────────────────────────────
        from slack.handlers import formatar_resumo_relatorio
        _msg_slack = f"{emoji} *{label} · Relatório {tipo_label}*\n_{periodo_str}_\n" + formatar_resumo_relatorio(resultado)
        ok_slack = postar_slack_fallback(_msg_slack)

        url = resultado.get("url", "—")

        # Confirma envio Slack
        if not ok_slack:
            logger.error("[%s] ❌ Slack: envio falhou após tentativas", cliente_key)
        else:
            logger.info("[%s] ✅ Slack: mensagem enviada com sucesso", cliente_key)

        logger.info("[%s] Slack: %s | Notion: %s", cliente_key, "OK" if ok_slack else "FALHOU", url)

        # Posta link do Notion como mensagem standalone (semanal, mensal e diário)
        if url and url != "—":
            tipo_label_notion = {"semana": "Semanal", "mes": "Mensal", "diario": "Diário"}.get(periodo, tipo_label)
            postar_slack_fallback(
                f"{emoji} *{label} · Relatório {tipo_label_notion} no Notion*\n"
                f"_{periodo_str}_\n"
                f"📎 <{url}|Abrir relatório completo no Notion>"
            )

        # ── Persiste log detalhado do relatório no SQLite ──────────────────────
        try:
            from db.relatorio_log import registrar_relatorio as _reg_rel
            from db.registry import get_cliente as _get_cli
            _cfg_cli  = _get_cli(cliente_key)
            _plataforma = _cfg_cli.get("plataforma", "meta_ads")
            _resumo   = resultado.get("resumo", {})
            # Resolve canal Slack efetivamente usado
            import os as _os
            _slack_ch = _os.getenv("SLACK_CHANNEL", "performance").lstrip("#")
            _reg_rel(
                cliente_key=cliente_key,
                plataforma=_plataforma,
                periodicidade=tipo_builder,  # 'diario', 'semanal', 'mensal'
                data_inicio=str(data_inicio),
                data_fim=str(data_fim),
                titulo=resultado.get("titulo"),
                notion_url=url if url != "—" else None,
                notion_page_id=resultado.get("page_id"),
                slack_ok=ok_slack,
                slack_channel=_slack_ch if ok_slack else None,
                blocos_n=len(resultado.get("blocos", [])),
                resumo=_resumo,
                status="ok",
                duracao_ms=_duracao_ms,
            )
        except Exception as _log_err:
            logger.warning("[%s] Falha ao registrar log de relatório: %s", cliente_key, _log_err)

        registrar_run(
            cliente_key=cliente_key,
            periodo=periodo,
            data_inicio=str(data_inicio),
            data_fim=str(data_fim),
            status="sucesso",
            notion_url=url,
            slack_ok=ok_slack,
            run_id=run_id,
        )
        return {"ok": True, "resultado": resultado}

    except Exception as e:
        logger.error("[%s] ERRO: %s", cliente_key, e)
        postar_slack_fallback(
            f"❌ *{label} · Relatório {tipo_label} — ERRO*\n_{e}_",
            max_tentativas=3,
        )
        registrar_run(
            cliente_key=cliente_key,
            periodo=periodo,
            data_inicio=str(data_inicio),
            data_fim=str(data_fim),
            status="erro",
            erro=str(e),
            run_id=run_id,
        )
        return {"ok": False, "erro": str(e)}



# ── Relatório multi-plataforma ancorado (Source OF e similares) ──────────────

def _postar_ancora_slack(emoji: str, label: str, tipo_label: str, periodo_str: str,
                         plataformas_ok: list, channel: str = None):
    """Posta mensagem âncora no canal. Retorna thread_ts ou None."""
    from slack.poster import _post_api, _channel
    ch = (channel or _channel()).lstrip("#")
    plat_str = " · ".join(plataformas_ok) if plataformas_ok else "todas as fontes"
    ancora = (
        f"{emoji} *{label} · Relatório {tipo_label}*\n"
        f"_{periodo_str}_\n"
        f"📡 Fontes: {plat_str}"
    )
    resp = _post_api("chat.postMessage", {"channel": ch, "text": ancora, "mrkdwn": True})
    if not resp.get("ok"):
        logger.error("[Âncora] Falha ao postar: %s", resp.get("error"))
        return None
    ts = resp["ts"]
    logger.info("[Âncora] Postada ts=%s", ts)
    return ts


def _postar_relatorio_em_thread(resultado: dict, emoji: str, label: str,
                                  tipo_label: str, periodo_str: str,
                                  thread_ts: str, channel: str = None) -> bool:
    """Posta resumo de uma plataforma na thread da âncora."""
    from slack.poster import _post_api, _channel
    from slack.handlers import formatar_resumo_relatorio
    ch = (channel or _channel()).lstrip("#")

    plataforma = resultado.get("plataforma", "")
    plat_labels = {"meta_ads": "Meta Ads", "google_ads": "Google Ads", "shopify": "Shopify"}
    titulo_plat = plat_labels.get(plataforma, plataforma.title())
    url = resultado.get("url", "—")

    resumo_fmt = formatar_resumo_relatorio(resultado)
    msg_notion = f"\n📎 <{url}|Abrir no Notion>" if url and url != "—" else ""
    texto = (
        f"*{emoji} {label} · {titulo_plat} · {tipo_label}*\n"
        f"_{periodo_str}_\n"
        f"{resumo_fmt}{msg_notion}"
    )
    resp = _post_api("chat.postMessage", {
        "channel":   ch,
        "thread_ts": thread_ts,
        "text":      texto,
        "mrkdwn":    True,
    })
    ok = resp.get("ok", False)
    logger.info("[Thread] %s->%s: %s", plataforma, thread_ts[:8],
                "OK" if ok else f"FALHOU: {resp.get('error')}")
    return ok


def executar_cliente_multi_plataforma(
    cliente_key: str,
    cfg: dict,
    periodo: str,
    data_inicio,
    data_fim,
    periodo_str: str,
    tipo_label: str,
    tipo_builder: str,
    publicar: bool,
    apenas_alertas: bool = False,
) -> dict:
    """
    Gera relatórios separados por plataforma (Google Ads, Meta Ads, Shopify)
    com estrutura âncora + threads no Slack.
    """
    from db.cron_log import registrar_run
    from db.registry import get_loader
    import time as _time

    nome  = cfg.get("nome", cliente_key)
    emoji = cfg.get("slack_emoji", "📊")
    label = cfg.get("slack_label", nome)

    logger.info("--- [%s] %s (multi-plataforma) ---", cliente_key, label)

    # Alertas proativos
    alertas = verificar_alertas(cliente_key, data_inicio, data_fim)
    for alerta in alertas:
        # Deduplicação: não reenvia alerta já postado hoje
        from db.relatorio_log import alerta_ja_enviado_hoje, registrar_alerta
        _data_ref = str(data_fim)
        if alerta_ja_enviado_hoje(cliente_key, _data_ref):
            logger.info("[%s] Alerta ROAS já enviado hoje (%s) — ignorando duplicata", cliente_key, _data_ref)
            continue
        ok = postar_alerta_ancorado(alerta)
        logger.info("[%s] Alerta postado: %s", cliente_key, "OK" if ok else "FALHOU")
        if ok:
            registrar_alerta(
                cliente_key=cliente_key,
                data_ref=_data_ref,
                campanhas_n=len(alerta.get("campanhas", [])),
                slack_ok=True,
            )

    if apenas_alertas:
        if not alertas:
            logger.info("[%s] Sem alertas.", cliente_key)
        return {"ok": True, "alertas": len(alertas)}

    run_id = registrar_run(
        cliente_key=cliente_key, periodo=periodo,
        data_inicio=str(data_inicio), data_fim=str(data_fim),
        status="iniciado",
    )

    # 1. Loader
    loader_fn = get_loader(cliente_key)
    _loader_ok = False
    for _attempt in range(1, 4):
        try:
            loader_fn()
            _loader_ok = True
            break
        except Exception as e:
            logger.warning("[%s] Loader tentativa %d/3 falhou: %s", cliente_key, _attempt, e)
            if _attempt < 3:
                _time.sleep(10)
    if not _loader_ok:
        logger.error("[%s] Loader falhou após 3 tentativas", cliente_key)
        registrar_run(cliente_key=cliente_key, periodo=periodo,
                      data_inicio=str(data_inicio), data_fim=str(data_fim),
                      status="erro", erro="Sync falhou após 3 tentativas", run_id=run_id)
        postar_slack_fallback(f"❌ *{label} · Sync FALHOU*\n_3 tentativas sem sucesso._")
        return {"ok": False, "erro": "Sync falhou após 3 tentativas"}

    # 2. Detecta plataformas com dados (dinâmico por cliente via clientes.json)
    # Reseta conexão federada após o sync para garantir que os dados novos são vistos
    try:
        from db.connections import reset_federated_conn
        reset_federated_conn()
    except Exception:
        pass
    from db.connection import query_one as _q
    data_ref = str(data_fim)
    plataformas_disponiveis = []

    def _val(row, key=0):
        """Extrai valor de query_one que retorna dict."""
        if not row: return None
        v = list(row.values())[0] if isinstance(row, dict) else (row[0] if row else None)
        return v

    # Detecta tabelas configuradas para este cliente
    plats_cfg = cfg.get("plataformas", {})

    # Google Ads
    gads_cfg = plats_cfg.get("google_ads", {})
    if gads_cfg.get("ativo", False):
        tabela_g = gads_cfg.get("tabela_db") or f"{cliente_key}_gads_diario"
        try:
            _r = _q(f"SELECT SUM(investimento) FROM {tabela_g} WHERE data=?", (data_ref,))
            _v = _val(_r)
            if _v:
                plataformas_disponiveis.append("google_ads")
                logger.info("[%s] Google Ads: dados OK (investimento=%.2f)", cliente_key, _v)
            else:
                logger.warning("[%s] Google Ads: sem dados para %s", cliente_key, data_ref)
        except Exception as _e:
            logger.warning("[%s] Google Ads: erro ao checar tabela %s: %s", cliente_key, tabela_g, _e)

    # Meta Ads
    meta_cfg = plats_cfg.get("meta_ads", {})
    if meta_cfg.get("ativo", False):
        tabela_m = meta_cfg.get("tabela_db") or f"{cliente_key}_meta_ads_diario"
        try:
            _r = _q(f"SELECT SUM(investimento) FROM {tabela_m} WHERE data=?", (data_ref,))
            _v = _val(_r)
            if _v:
                plataformas_disponiveis.append("meta_ads")
                logger.info("[%s] Meta Ads: dados OK (investimento=%.2f)", cliente_key, _v)
            else:
                logger.warning("[%s] Meta Ads: sem dados para %s", cliente_key, data_ref)
        except Exception as _e:
            logger.warning("[%s] Meta Ads: erro ao checar tabela %s: %s", cliente_key, tabela_m, _e)

    # Shopify (apenas source_of por ora)
    if cliente_key == "source_of":
        try:
            _r = _q("SELECT SUM(receita) FROM shopify_receita_diaria WHERE data=?", (data_ref,))
            _v = _val(_r)
            if _v:
                plataformas_disponiveis.append("shopify")
                logger.info("[%s] Shopify: dados OK (receita=%.2f)", cliente_key, _v)
            else:
                logger.warning("[%s] Shopify: sem dados para %s", cliente_key, data_ref)
        except Exception as _e:
            logger.warning("[%s] Shopify: erro: %s", cliente_key, _e)

    if not plataformas_disponiveis:
        msg = f"Sem dados em nenhuma plataforma para {cliente_key} em {data_ref}"
        logger.error("[%s] ❌ %s", cliente_key, msg)
        registrar_run(cliente_key=cliente_key, periodo=periodo,
                      data_inicio=str(data_inicio), data_fim=str(data_fim),
                      status="erro", erro=msg, run_id=run_id)
        postar_slack_fallback(f"❌ *{label} · Sem dados para {data_ref}*")
        return {"ok": False, "erro": msg}

    # 2b. Guardrails de qualidade de dados (volume mínimo + coerência numérica + janela de atribuição)
    from agent.queries import validar_cobertura_dados, aviso_janela_atribuicao
    avisos_qualidade = []
    plataformas_parciais = []

    for _plat in [p for p in plataformas_disponiveis if p != "shopify"]:
        _cob = validar_cobertura_dados(data_ref, cliente_key=cliente_key, plataforma=_plat)
        if not _cob["ok"] and _cob["motivo"] != "sem_dados":
            logger.warning("[%s] ⚠️ Dado parcial %s: %s", cliente_key, _plat, _cob["motivo"])
            avisos_qualidade.append(_cob["aviso"])
            plataformas_parciais.append(_plat)

    _aviso_janela = aviso_janela_atribuicao(
        data_ref,
        plataforma=plataformas_disponiveis[0] if len(plataformas_disponiveis) == 1 else None,
    )
    if _aviso_janela:
        avisos_qualidade.append(_aviso_janela)

    # Se dados parciais detectados: aborta e agenda retry na mensagem
    if plataformas_parciais:
        _aviso_str = "\n\n".join(avisos_qualidade)
        registrar_run(cliente_key=cliente_key, periodo=periodo,
                      data_inicio=str(data_inicio), data_fim=str(data_fim),
                      status="sem_dados", erro=f"dado_parcial: {plataformas_parciais}", run_id=run_id)
        postar_slack_fallback(
            f"⏳ *{label} · Dado incompleto*\n{_aviso_str}"
        )
        return {"ok": False, "erro": f"dado_parcial: {plataformas_parciais}"}

    plat_labels_map = {"google_ads": "Google Ads", "meta_ads": "Meta Ads", "shopify": "Shopify"}
    plats_str_list = [plat_labels_map.get(p, p) for p in plataformas_disponiveis]
    thread_ts = _postar_ancora_slack(
        emoji=emoji, label=label, tipo_label=tipo_label,
        periodo_str=periodo_str, plataformas_ok=plats_str_list,
    )
    if not thread_ts:
        logger.error("[%s] Âncora Slack falhou — abortando", cliente_key)
        registrar_run(cliente_key=cliente_key, periodo=periodo,
                      data_inicio=str(data_inicio), data_fim=str(data_fim),
                      status="erro", erro="Âncora Slack falhou", run_id=run_id)
        return {"ok": False, "erro": "Âncora Slack falhou"}

    # 4. Gera e posta cada plataforma na thread
    from relatorio.builder_diario import gerar_relatorio_diario
    resultados_plats = {}
    erros_plats = {}

    for plataforma in plataformas_disponiveis:
        logger.info("[%s] Gerando relatório %s...", cliente_key, plataforma)
        try:
            if plataforma == "shopify":
                from relatorio.builder_diario import _buscar_shopify_dia, _blocos_shopify
                from notion.historico import criar_relatorio_no_historico

                shopify_data = _buscar_shopify_dia(data_ref)
                if not shopify_data or not shopify_data.get("receita"):
                    raise ValueError(f"Shopify sem dados para {data_ref}")

                data_fmt = f"{data_ref[8:10]}/{data_ref[5:7]}/{data_ref[:4]}"
                titulo_shop = f"{emoji} {nome} · Shopify · Relatório Diário · {data_fmt}"
                blocos_shop = _blocos_shopify(shopify_data)

                resultado_shop = {
                    "titulo":      titulo_shop,
                    "resumo":      {"investimento": None,
                                    "receita_shopify": shopify_data.get("receita"),
                                    "pedidos": shopify_data.get("pedidos"),
                                    "shopify": shopify_data},
                    "blocos":      blocos_shop,
                    "data":        data_ref,
                    "cliente_key": cliente_key,
                    "plataforma":  "shopify",
                }

                if publicar:
                    from datetime import date as _date
                    data_obj = _date.fromisoformat(data_ref)
                    r_notion = criar_relatorio_no_historico(
                        titulo=titulo_shop, blocos=blocos_shop,
                        periodo_str=data_fmt, tipo="diario",
                        data_ref=data_obj, data_inicio=data_obj,
                        data_fim=data_obj, cliente_key=cliente_key,
                        plataforma="shopify",
                    )
                    resultado_shop["url"]     = r_notion.get("url")
                    resultado_shop["page_id"] = r_notion.get("page_id")

                resultados_plats["shopify"] = resultado_shop

            else:
                resultado_ads = gerar_relatorio_diario(
                    cliente_key=cliente_key, plataforma=plataforma,
                    data=data_ref, publicar=publicar,
                )
                if resultado_ads.get("erro"):
                    err = resultado_ads['erro']
                    if err == "sem_dados":
                        raise ValueError(f"sem_dados (API ainda não sincronizou {data_ref})")
                    raise ValueError(f"Builder: {err}")

                _r = resultado_ads.get("resumo") or {}
                _falhas = []
                if not _r: _falhas.append("resumo ausente")
                if not _r.get("investimento"): _falhas.append("investimento zerado/ausente")
                if not resultado_ads.get("titulo"): _falhas.append("título ausente")
                if not resultado_ads.get("blocos"): _falhas.append("blocos vazios")
                if publicar and not resultado_ads.get("url"): _falhas.append("URL Notion ausente")
                if _falhas:
                    raise ValueError(f"Validação falhou: {'; '.join(_falhas)}")

                resultados_plats[plataforma] = resultado_ads

            logger.info("[%s] ✅ %s: OK", cliente_key, plataforma)

        except Exception as exc:
            logger.error("[%s] ❌ %s: %s", cliente_key, plataforma, exc)
            erros_plats[plataforma] = str(exc)

    if not resultados_plats:
        # Distingue delay de sync de erros reais
        todos_sem_dados = all("sem_dados" in str(e) for e in erros_plats.values())
        if todos_sem_dados:
            msg = f"Dados de {data_ref} ainda n\u00e3o sincronizados pela API (delay normal)"
            status_run = "sem_dados"
            slack_msg  = f"\u23f3 *{label} \u00b7 Aguardando sincroniza\u00e7\u00e3o*\n_Dados de {data_ref} ainda n\u00e3o chegaram \u2014 APIs de an\u00fancios t\u00eam delay de ~24h. O relat\u00f3rio ser\u00e1 gerado automaticamente quando os dados chegarem._"
        else:
            msg = f"Todos os relat\u00f3rios falharam: {erros_plats}"
            status_run = "erro"
            slack_msg  = f"\u274c *{label} \u00b7 Todos os relat\u00f3rios falharam*\n_{msg}_"
        registrar_run(cliente_key=cliente_key, periodo=periodo,
                      data_inicio=str(data_inicio), data_fim=str(data_fim),
                      status=status_run, erro=msg, run_id=run_id)
        postar_slack_fallback(slack_msg)
        return {"ok": False, "erro": msg}

    # 5. Posta cada plataforma na thread da âncora
    slack_ok_count = 0
    for plataforma, resultado in resultados_plats.items():
        ok_thread = _postar_relatorio_em_thread(
            resultado=resultado, emoji=emoji, label=label,
            tipo_label=tipo_label, periodo_str=periodo_str,
            thread_ts=thread_ts,
        )
        if ok_thread:
            slack_ok_count += 1

    slack_ok = slack_ok_count > 0

    # Avisa fontes com erro na thread
    if erros_plats:
        logger.warning("[%s] ⚠️ Fontes com erro: %s", cliente_key, list(erros_plats))
        from slack.poster import _post_api, _channel
        _ch = _channel().lstrip("#")
        _erros_str = ", ".join(f"{p}: {e[:60]}" for p, e in erros_plats.items())
        _post_api("chat.postMessage", {
            "channel":   _ch,
            "thread_ts": thread_ts,
            "text":      f"⚠️ Fontes com falha (dados podem estar desatualizados): {_erros_str}",
            "mrkdwn":    True,
        })

    registrar_run(
        cliente_key=cliente_key, periodo=periodo,
        data_inicio=str(data_inicio), data_fim=str(data_fim),
        status="sucesso" if slack_ok else "erro",
        notion_url=next((r.get("url") for r in resultados_plats.values() if r.get("url")), None),
        slack_ok=slack_ok, run_id=run_id,
    )

    logger.info(
        "[%s] ✅ Multi-plataforma: %d/%d OK | Slack: %s",
        cliente_key, len(resultados_plats), len(plataformas_disponiveis),
        "OK" if slack_ok else "FALHOU",
    )
    return {"ok": slack_ok, "resultados": resultados_plats, "erros": erros_plats}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cron — Relatórios Todos os Clientes")
    parser.add_argument("--periodo", "-p", choices=["diario", "semana", "mes"], default="semana")
    parser.add_argument("--no-notion", action="store_true", help="Não publica no Notion")
    parser.add_argument("--cliente", default="todos",
                        help="Chave do cliente (ex: source_of) ou 'todos'")
    parser.add_argument("--apenas-alertas", action="store_true",
                        help="Só checa alertas de ROAS, sem gerar relatório completo")
    parser.add_argument("--apenas-loader", action="store_true",
                        help="Só executa o loader (sincroniza dados), sem relatório nem alertas")
    parser.add_argument("--data-inicio", default=None,
                        help="Sobrescreve data de início do período (formato YYYY-MM-DD)")
    parser.add_argument("--data-fim", default=None,
                        help="Sobrescreve data de fim do período (formato YYYY-MM-DD)")
    args = parser.parse_args()

    publicar       = not args.no_notion
    apenas_alertas = args.apenas_alertas
    apenas_loader  = args.apenas_loader
    data_inicio, data_fim = resolver_periodo(args.periodo)
    # Sobrescreve datas se passadas manualmente
    if args.data_inicio:
        data_inicio = date.fromisoformat(args.data_inicio)
    if args.data_fim:
        data_fim = date.fromisoformat(args.data_fim)
    periodo_str  = f"{data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"
    tipo_label   = {"diario": "Diário", "semana": "Semanal", "mes": "Mensal"}.get(args.periodo, args.periodo.title())
    tipo_builder = {"mes": "mensal", "semana": "semanal", "diario": "diario"}.get(args.periodo, "semanal")

    logger.info("=== Cron Todos os Clientes — %s (%s) ===", tipo_label, periodo_str)

    from db.registry import get_clientes_ativos, listar_clientes
    if args.cliente == "todos":
        clientes = get_clientes_ativos()
    else:
        todos = listar_clientes()
        if args.cliente not in todos:
            logger.error("Cliente '%s' não encontrado. Disponíveis: %s", args.cliente, list(todos))
            sys.exit(1)
        clientes = {args.cliente: todos[args.cliente]}

    resultados, erros = {}, {}

    for i, (cliente_key, cfg) in enumerate(clientes.items(), 1):
        logger.info("--- [%d/%d] %s ---", i, len(clientes), cfg.get("nome", cliente_key))

        # Modo apenas-loader: só sincroniza banco, sem relatório nem alertas
        if apenas_loader:
            try:
                from db.registry import get_loader
                loader_fn = get_loader(cliente_key)
                loader_fn()
                logger.info("[%s] Loader OK", cliente_key)
                resultados[cliente_key] = {"ok": True}
            except Exception as e:
                logger.error("[%s] Loader falhou: %s", cliente_key, e)
                erros[cliente_key] = str(e)
            continue

        # Clientes com múltiplas fontes usam fluxo multi-plataforma ancorado
        _kwargs = dict(
            cliente_key=cliente_key, cfg=cfg,
            periodo=args.periodo,
            data_inicio=data_inicio, data_fim=data_fim,
            periodo_str=periodo_str, tipo_label=tipo_label, tipo_builder=tipo_builder,
            publicar=publicar, apenas_alertas=apenas_alertas,
        )
        if cfg.get("multi_plataforma") and args.periodo == "diario":
            res = executar_cliente_multi_plataforma(**_kwargs)
        else:
            res = executar_cliente(**_kwargs)
        if res.get("ok"):
            resultados[cliente_key] = res
        else:
            erros[cliente_key] = res.get("erro", "erro desconhecido")

    total    = len(resultados) + len(erros)
    ok_count = len(resultados)
    logger.info("=== Concluído: %d/%d com sucesso ===", ok_count, total)

    if erros:
        logger.error("Erros: %s", erros)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
