"""
scripts/cron_relatorio.py

Executado pelo OpenClaw Cron (isolated mode).
Gera o relatório Meta Ads e posta resumo no Slack.

Uso:
    python scripts/cron_relatorio.py --periodo semana
    python scripts/cron_relatorio.py --periodo mes
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


def _brl(v) -> str:
    if v is None:
        return "—"
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _x(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):.2f}x"


def _pct(v) -> str:
    if v is None:
        return "—"
    return f"{float(v):.2f}%"


def resolver_periodo(periodo: str):
    hoje = date.today()
    if periodo == "semana":
        seg = hoje - timedelta(days=hoje.weekday() + 7)
        return seg, seg + timedelta(days=6)
    elif periodo == "mes":
        primeiro_este = hoje.replace(day=1)
        ultimo_mes = primeiro_este - timedelta(days=1)
        return ultimo_mes.replace(day=1), ultimo_mes
    raise ValueError(f"Período inválido: {periodo}")


def postar_slack(msg: str):
    """Posta mensagem no canal Slack via Webhook."""
    import urllib.request
    import json

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL não definida — não postou no Slack")
        return False

    payload = json.dumps({"text": msg}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
            logger.info("Slack webhook: status %d", resp.status)
            return ok
    except Exception as e:
        logger.error("Erro ao postar no Slack: %s", e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Cron — Relatório Meta Ads")
    parser.add_argument(
        "--periodo", "-p",
        choices=["semana", "mes"],
        default="semana",
    )
    args = parser.parse_args()

    data_inicio, data_fim = resolver_periodo(args.periodo)
    periodo_str = f"{data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"
    tipo = "Semanal" if args.periodo == "semana" else "Mensal"

    logger.info("Cron %s iniciado: %s", tipo, periodo_str)

    from relatorio.builder import gerar_relatorio_semanal

    resultado = gerar_relatorio_semanal(
        cliente="source_of",
        data_inicio=data_inicio,
        data_fim=data_fim,
        publicar=True,
    )

    if "erro" in resultado:
        msg = f"❌ *Relatório {tipo} — ERRO*\n{resultado['erro']}"
        postar_slack(msg)
        logger.error("Erro: %s", resultado["erro"])
        sys.exit(1)

    r   = resultado.get("resumo", {})
    url = resultado.get("url") or ""

    roas_str = _x(r.get("roas"))
    ctr_str  = _pct(r.get("ctr"))
    invest_str = _brl(r.get("total_invest"))
    invest_imp_str = _brl(r.get("invest_imp"))
    cpa_str  = _brl(r.get("cpa"))

    alertas      = resultado.get("alertas", [])
    alerta_txt   = "\n".join(f"  {a}" for a in alertas)
    num_campanhas = resultado.get("num_campanhas", 0)
    compras      = r.get("total_compras", "—")

    titulo = f"📊 Relatório {tipo} — Source OF · Meta Ads\n{periodo_str}"
    corpo = (
        f"*{titulo}*\n\n"
        f"📊 *Campanhas:* {num_campanhas}\n"
        f"💰 *Investimento:* {invest_str}  (+imposto: {invest_imp_str})\n"
        f"📈 *ROAS:* {roas_str}\n"
        f"🛒 *Compras:* {compras}\n"
        f"💵 *CPA:* {cpa_str}\n"
        f"🎯 *CTR:* {ctr_str}\n\n"
        f"🚨 *Alertas:*\n{alerta_txt}\n"
    )

    if url:
        corpo += f"\n📎 <{url}|Abrir relatório no Notion>"

    ok = postar_slack(corpo)
    logger.info("Cron %s concluído. Slack: %s | Notion: %s", tipo, "OK" if ok else "FALHOU", url or "—")

    # Exit 0 = sucesso
    sys.exit(0)


if __name__ == "__main__":
    main()
