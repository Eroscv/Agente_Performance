"""
scripts/cron_todos_relatorios.py

Dispara relatórios para TODOS os clientes ativos em sequência:
  1. Source OF — Meta Ads (semanal ou mensal)
  2. POD Kombucha — Google Ads (semanal ou mensal)

Cada cliente tem seu próprio bloco de erro isolado — falha de um não
impede o outro.

Uso:
    python scripts/cron_todos_relatorios.py --periodo semana
    python scripts/cron_todos_relatorios.py --periodo mes
    python scripts/cron_todos_relatorios.py --periodo diario
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


# ── Formatadores ──────────────────────────────────────────────────────────────

def _fmt_brl(v) -> str:
    if v is None:
        return "—"
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


def _fmt_x(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}x"
    except Exception:
        return "—"


def _fmt_n(v, decimais=0) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{decimais}f}".replace(",", ".")
    except Exception:
        return "—"


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


# ── Slack ─────────────────────────────────────────────────────────────────────

def postar_slack(msg: str, max_tentativas: int = 3, delay_inicial: float = 5.0, delay_maximo: float = 60.0) -> bool:
    import urllib.request
    import json
    import time

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL não definida")
        return False

    payload = json.dumps({"text": msg}).encode("utf-8")
    tentativa = 0
    delay = delay_inicial

    while True:
        tentativa += 1
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return True
                logger.warning("Slack status inesperado: %d (tentativa %d)", resp.status, tentativa)
        except Exception as e:
            logger.warning("Erro Slack (tentativa %d): %s", tentativa, e)

        if max_tentativas is not None and tentativa >= max_tentativas:
            logger.error("Slack: desistindo após %d tentativas.", tentativa)
            return False

        time.sleep(delay)
        delay = min(delay * 2, delay_maximo)


# ── Formatadores de resumo ────────────────────────────────────────────────────

def _resumo_source_of(resultado: dict, periodo_str: str, tipo_label: str) -> str:
    r = resultado.get("resumo", {})
    url = resultado.get("url") or ""
    link = f"\n📎 <{url}|Abrir no Notion>" if url else ""

    roas_str = f"{r['roas']:.2f}x" if r.get("roas") else "—"
    ctr_str  = f"{r['ctr']:.2f}%" if r.get("ctr") else "—"

    return (
        f"🔵 *Source OF · Meta Ads · Relatório {tipo_label}*\n\n"
        f"📅 *Período:* {periodo_str}\n"
        f"📊 *Campanhas:* {resultado.get('num_campanhas', '—')}\n"
        f"💰 *Investimento:* {_fmt_brl(r.get('total_invest'))}  (+imposto: {_fmt_brl(r.get('invest_imp'))})\n"
        f"📈 *ROAS:* {roas_str}\n"
        f"🛒 *Compras:* {r.get('total_compras', '—')}\n"
        f"💵 *CPA:* {_fmt_brl(r.get('cpa'))}\n"
        f"🎯 *CTR:* {ctr_str}"
        f"{link}"
    )


def _resumo_pod(resultado: dict, periodo_str: str, tipo_label: str) -> str:
    r = resultado.get("resumo", {})
    url = resultado.get("url") or ""
    link = f"\n📎 <{url}|Abrir no Notion>" if url else ""

    return (
        f"🟢 *POD Kombucha · Google Ads · Relatório {tipo_label}*\n\n"
        f"📅 *Período:* {periodo_str}\n"
        f"📊 *Campanhas:* {_fmt_n(r.get('num_campanhas'))}\n"
        f"💰 *Investimento:* {_fmt_brl(r.get('invest'))}\n"
        f"👁️ *Impressões:* {_fmt_n(r.get('impressoes'))}\n"
        f"🖱️ *Cliques:* {_fmt_n(r.get('cliques'))}\n"
        f"🛒 *Conversões:* {_fmt_n(r.get('conversoes'), 1)}\n"
        f"💵 *Receita Google:* {_fmt_brl(r.get('receita'))}\n"
        f"📈 *ROAS:* {_fmt_x(r.get('roas'))}\n"
        f"💲 *CPA:* {_fmt_brl(r.get('cpa'))}"
        f"{link}"
    )


# ── Pipeline ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cron — Relatórios Todos os Clientes")
    parser.add_argument(
        "--periodo", "-p",
        choices=["diario", "semana", "mes"],
        default="semana",
    )
    parser.add_argument(
        "--no-notion",
        action="store_true",
        help="Gera relatórios mas não publica no Notion",
    )
    parser.add_argument(
        "--cliente",
        choices=["source_of", "pod_kombucha", "todos"],
        default="todos",
        help="Gera apenas para o cliente especificado (padrão: todos)",
    )
    args = parser.parse_args()

    publicar = not args.no_notion
    data_inicio, data_fim = resolver_periodo(args.periodo)
    periodo_str = f"{data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"
    tipo_label = {"diario": "Diário", "semana": "Semanal", "mes": "Mensal"}.get(args.periodo, args.periodo.title())
    tipo_builder = "mensal" if args.periodo == "mes" else "semanal"

    logger.info("=== Cron Todos os Clientes — %s (%s) ===", tipo_label, periodo_str)

    resultados = {}
    erros = {}

    # ── 1. Source OF — Meta Ads ───────────────────────────────────────────
    if args.cliente in ("source_of", "todos"):
        logger.info("--- [1/2] Source OF · Meta Ads ---")
        try:
            from relatorio.builder import gerar_relatorio_semanal, gerar_relatorio_mensal

            if tipo_builder == "mensal":
                res_source = gerar_relatorio_mensal(cliente="source_of", publicar=publicar)
            else:
                res_source = gerar_relatorio_semanal(
                    cliente="source_of",
                    data_inicio=data_inicio,
                    data_fim=data_fim,
                    publicar=publicar,
                )

            if res_source.get("erro"):
                raise RuntimeError(res_source["erro"])

            resultados["source_of"] = res_source
            from slack.poster import postar_relatorio, postar_webhook_fallback
            ok = postar_relatorio(res_source, periodo_str, tipo_label, cliente="source_of")
            if not ok:
                ok = postar_webhook_fallback(_resumo_source_of(res_source, periodo_str, tipo_label))
            logger.info("[Source OF] Slack: %s | Notion: %s",
                        "OK" if ok else "FALHOU", res_source.get("url", "—"))

        except Exception as e:
            erros["source_of"] = str(e)
            logger.error("[Source OF] ERRO: %s", e)
            postar_slack(
                f"❌ *Source OF · Meta Ads · Relatório {tipo_label} — ERRO*\n_{e}_",
                max_tentativas=3,
            )

    # ── 2. POD Kombucha — Google Ads ─────────────────────────────────────
    if args.cliente in ("pod_kombucha", "todos"):
        logger.info("--- [2/2] POD Kombucha · Google Ads ---")
        try:
            from relatorio.builder_pod import gerar_relatorio_pod

            res_pod = gerar_relatorio_pod(
                data_inicio=data_inicio,
                data_fim=data_fim,
                tipo=tipo_builder,
                publicar=publicar,
            )

            if res_pod.get("erro"):
                raise RuntimeError(res_pod["erro"])

            resultados["pod_kombucha"] = res_pod
            from slack.poster import postar_relatorio, postar_webhook_fallback
            ok = postar_relatorio(res_pod, periodo_str, tipo_label, cliente="pod_kombucha")
            if not ok:
                ok = postar_webhook_fallback(_resumo_pod(res_pod, periodo_str, tipo_label))
            logger.info("[POD] Slack: %s | Notion: %s",
                        "OK" if ok else "FALHOU", res_pod.get("url", "—"))

        except Exception as e:
            erros["pod_kombucha"] = str(e)
            logger.error("[POD] ERRO: %s", e)
            postar_slack(
                f"❌ *POD Kombucha · Google Ads · Relatório {tipo_label} — ERRO*\n_{e}_",
                max_tentativas=3,
            )

    # ── Sumário final ─────────────────────────────────────────────────────
    total = len(resultados) + len(erros)
    ok_count = len(resultados)
    logger.info("=== Concluído: %d/%d com sucesso ===", ok_count, total)

    if erros:
        logger.error("Erros: %s", erros)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
