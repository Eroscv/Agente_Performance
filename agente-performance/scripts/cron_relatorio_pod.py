"""
scripts/cron_relatorio_pod.py

⚠️  CLIENTE: POD KOMBUCHA — Google Ads exclusivo.
    NUNCA misturar com dados da Source Of ou qualquer outro cliente.

Executado pelo OpenClaw Cron (isolated mode) ou manualmente.
Gera o relatório Google Ads do POD Kombucha e posta resumo no Slack.

Uso:
    python scripts/cron_relatorio_pod.py --periodo semana
    python scripts/cron_relatorio_pod.py --periodo mes
    python scripts/cron_relatorio_pod.py --periodo diario
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


def postar_slack(msg: str, max_tentativas: int = None, delay_inicial: float = 5.0, delay_maximo: float = 300.0) -> bool:
    """
    Posta mensagem no canal Slack via Webhook com retry exponencial.
    """
    import urllib.request
    import json
    import time

    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL não definida — não postou no Slack")
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
                    logger.info("Slack webhook: status %d (tentativa %d)", resp.status, tentativa)
                    return True
                else:
                    logger.warning(
                        "Slack webhook: status inesperado %d (tentativa %d)",
                        resp.status, tentativa,
                    )
        except Exception as e:
            logger.warning("Erro ao postar no Slack (tentativa %d): %s", tentativa, e)

        if max_tentativas is not None and tentativa >= max_tentativas:
            logger.error("Slack webhook: desistindo após %d tentativas.", tentativa)
            return False

        logger.info("Aguardando %.0fs antes de retentativa %d...", delay, tentativa + 1)
        time.sleep(delay)
        delay = min(delay * 2, delay_maximo)


def formatar_resumo_pod(resultado: dict, periodo_str: str, tipo: str) -> str:
    """Formata resumo do relatório POD para Slack (mrkdwn)."""
    r = resultado.get("resumo", {})
    url = resultado.get("url") or ""
    tipo_label = {"diario": "Diário", "semana": "Semanal", "mes": "Mensal"}.get(tipo, tipo.title())

    link_notion = f"\n📎 <{url}|Abrir relatório no Notion>" if url else ""

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
        f"{link_notion}"
    )


def main():
    parser = argparse.ArgumentParser(description="Cron — Relatório Google Ads POD Kombucha")
    parser.add_argument(
        "--periodo", "-p",
        choices=["diario", "semana", "mes"],
        default="semana",
    )
    parser.add_argument(
        "--no-notion",
        action="store_true",
        help="Gera relatório mas não publica no Notion",
    )
    args = parser.parse_args()

    publicar = not args.no_notion
    data_inicio, data_fim = resolver_periodo(args.periodo)
    periodo_str = f"{data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"
    tipo_label = {"diario": "Diário", "semana": "Semanal", "mes": "Mensal"}.get(args.periodo, args.periodo.title())

    logger.info("[POD] Cron %s iniciado: %s", tipo_label, periodo_str)

    from relatorio.builder_pod import gerar_relatorio_pod

    tipo_builder = "mensal" if args.periodo == "mes" else "semanal"
    resultado = gerar_relatorio_pod(
        data_inicio=data_inicio,
        data_fim=data_fim,
        tipo=tipo_builder,
        publicar=publicar,
    )

    if resultado.get("erro"):
        msg = f"❌ *POD Kombucha · Relatório {tipo_label} — ERRO*\n_{resultado['erro']}_"
        postar_slack(msg, max_tentativas=3)
        logger.error("[POD] Erro: %s", resultado["erro"])
        sys.exit(1)

    corpo = formatar_resumo_pod(resultado, periodo_str, args.periodo)
    ok = postar_slack(corpo)

    url = resultado.get("url", "—")
    logger.info("[POD] Cron %s concluído. Slack: %s | Notion: %s",
                tipo_label, "OK" if ok else "FALHOU", url)
    sys.exit(0)


if __name__ == "__main__":
    main()
