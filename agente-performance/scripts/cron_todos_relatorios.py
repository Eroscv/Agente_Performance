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
import sqlite3
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

DB_PATH = Path(__file__).parent.parent / "data" / "campanhas.db"


# ── Formatadores ──────────────────────────────────────────────────────────────

def _fmt_brl(v) -> str:
    if v is None: return "—"
    try: return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except: return "—"


def _fmt_x(v) -> str:
    if v is None: return "—"
    try: return f"{float(v):.2f}x"
    except: return "—"


def _fmt_n(v, decimais=0) -> str:
    if v is None: return "—"
    try: return f"{float(v):,.{decimais}f}".replace(",", ".")
    except: return "—"


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
    import urllib.request, json, time
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url: return False
    payload = json.dumps({"text": msg}).encode()
    for i in range(1, max_tentativas + 1):
        try:
            req = urllib.request.Request(webhook_url, data=payload,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200: return True
        except Exception as e:
            logger.warning("Slack webhook (tentativa %d): %s", i, e)
            if i < max_tentativas: import time; time.sleep(2 ** i)
    return False


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

        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        if is_gads:
            row = conn.execute("""
                SELECT
                    SUM(investimento) AS invest,
                    CASE WHEN SUM(investimento) > 0
                         THEN SUM(valor_conversao) / SUM(investimento) END AS roas
                FROM pod_gads_diario
                WHERE data_inicio BETWEEN ? AND ?
            """, (str(ant_ini), str(ant_fim))).fetchone()
        else:
            row = conn.execute("""
                SELECT
                    SUM(investimento) AS invest,
                    CASE WHEN SUM(investimento) > 0
                         THEN SUM(valor_compras) / SUM(investimento) END AS roas
                FROM campanhas
                WHERE data_inicio BETWEEN ? AND ?
            """, (str(ant_ini), str(ant_fim))).fetchone()

        conn.close()
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
        cfg     = get_cliente(cliente_key)
        is_gads = cfg.get("plataforma") == "google_ads"
        nome    = cfg.get("nome", cliente_key)
        emoji   = cfg.get("slack_emoji", "📊")
        threshold = ALERTA_ROAS_MIN_GADS if is_gads else ALERTA_ROAS_MIN

        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        if is_gads:
            rows = conn.execute("""
                SELECT campanha,
                       SUM(investimento) AS invest,
                       SUM(valor_conversao) AS receita,
                       CASE WHEN SUM(investimento) > 0
                            THEN SUM(valor_conversao) / SUM(investimento) END AS roas
                FROM pod_gads_diario
                WHERE data_inicio BETWEEN ? AND ?
                  AND investimento > 0
                GROUP BY campanha
                HAVING roas < ? OR roas IS NULL
                ORDER BY invest DESC
                LIMIT 5
            """, (str(data_inicio), str(data_fim), threshold)).fetchall()
        else:
            rows = conn.execute("""
                SELECT campanha,
                       SUM(investimento) AS invest,
                       SUM(valor_compras) AS receita,
                       CASE WHEN SUM(investimento) > 0
                            THEN SUM(valor_compras) / SUM(investimento) END AS roas
                FROM campanhas
                WHERE data_inicio BETWEEN ? AND ?
                  AND investimento > 0
                GROUP BY campanha
                HAVING roas < ? OR roas IS NULL
                ORDER BY invest DESC
                LIMIT 5
            """, (str(data_inicio), str(data_fim), threshold)).fetchall()

        conn.close()

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
        ok = postar_alerta_ancorado(alerta)  # âncora + thread detalhada
        logger.info("[%s] Alerta postado (ancorado): %s", cliente_key, "OK" if ok else "FALHOU")

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
        # ── Loader ────────────────────────────────────────────────────────
        from db.registry import get_loader, get_builder
        loader_fn = get_loader(cliente_key)
        try:
            loader_fn()
            import os as _os; _os.environ["SKIP_SHEETS_SYNC"] = "1"  # item 14: evita dupla sync
        except Exception as e:
            logger.warning("[%s] Loader falhou: %s — usando dados locais", cliente_key, e)

        # ── Builder ───────────────────────────────────────────────────────
        builder_fn = get_builder(cliente_key, tipo_builder)
        resultado  = builder_fn(data_inicio=data_inicio, data_fim=data_fim, publicar=publicar)
        import os as _os2; _os2.environ.pop("SKIP_SHEETS_SYNC", None)  # limpa flag

        if resultado.get("erro"):
            raise RuntimeError(resultado["erro"])

        # ── Item 9: Delta vs período anterior ─────────────────────────────
        delta = _calcular_delta(resultado, data_inicio, data_fim, cliente_key)

        # ── Slack âncora + thread ─────────────────────────────────────────
        from slack.poster import postar_relatorio
        ok_slack = postar_relatorio(resultado, periodo_str, tipo_label, cliente=cliente_key, delta=delta)
        if not ok_slack:
            postar_slack_fallback(
                f"{emoji} *{label} · Relatório {tipo_label}*\n_{periodo_str}_\n"
                f"Invest.: {_fmt_brl(resultado.get('resumo', {}).get('invest') or resultado.get('resumo', {}).get('total_invest'))}"
                f" · ROAS: {_fmt_x(resultado.get('resumo', {}).get('roas'))}"
            )

        url = resultado.get("url", "—")
        logger.info("[%s] Slack: %s | Notion: %s", cliente_key, "OK" if ok_slack else "FALHOU", url)

        # Posta link do Notion como mensagem standalone (somente no semanal)
        if periodo == "semana" and url and url != "—":
            postar_slack_fallback(
                f"{emoji} *{label} · Relatório Semanal no Notion*\n"
                f"_{periodo_str}_\n"
                f"📎 <{url}|Abrir relatório completo no Notion>"
            )

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
    args = parser.parse_args()

    publicar       = not args.no_notion
    apenas_alertas = args.apenas_alertas
    apenas_loader  = args.apenas_loader
    data_inicio, data_fim = resolver_periodo(args.periodo)
    periodo_str  = f"{data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"
    tipo_label   = {"diario": "Diário", "semana": "Semanal", "mes": "Mensal"}.get(args.periodo, args.periodo.title())
    tipo_builder = "mensal" if args.periodo == "mes" else "semanal"

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

        res = executar_cliente(
            cliente_key=cliente_key, cfg=cfg,
            periodo=args.periodo,
            data_inicio=data_inicio, data_fim=data_fim,
            periodo_str=periodo_str, tipo_label=tipo_label, tipo_builder=tipo_builder,
            publicar=publicar, apenas_alertas=apenas_alertas,
        )
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
