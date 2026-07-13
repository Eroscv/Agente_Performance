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


def postar_slack(msg: str, max_tentativas: int = None, delay_inicial: float = 5.0, delay_maximo: float = 300.0) -> bool:
    """
    Posta mensagem no canal Slack via Webhook.
    Faz retry infinito (ou até max_tentativas) com backoff exponencial até funcionar.

    Args:
        msg:             Texto da mensagem.
        max_tentativas:  None = retry infinito até sucesso. Inteiro limita as tentativas.
        delay_inicial:   Segundos de espera antes da 1ª retentativa.
        delay_maximo:    Teto do backoff exponencial (segundos).
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

        # Checa limite de tentativas
        if max_tentativas is not None and tentativa >= max_tentativas:
            logger.error("Slack webhook: desistindo após %d tentativas.", tentativa)
            return False

        logger.info("Aguardando %.0fs antes de retentativa %d...", delay, tentativa + 1)
        time.sleep(delay)
        delay = min(delay * 2, delay_maximo)


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

    from relatorio.builder import gerar_relatorio_semanal, gerar_relatorio_mensal
    from slack.handlers import formatar_resumo_relatorio

    if args.periodo == "mes":
        resultado = gerar_relatorio_mensal(
            cliente="source_of",
            publicar=True,
        )
    else:
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

    # Reutiliza formatar_resumo_relatorio (compartilhado com /relatorio do Slack)
    titulo = f"📊 Relatório {tipo} — Source OF · Meta Ads\n{periodo_str}"
    corpo = f"*{titulo}*\n\n" + formatar_resumo_relatorio(resultado)

    ok = postar_slack(corpo)
    logger.info("Cron %s concluído. Slack: %s | Notion: %s", tipo, "OK" if ok else "FALHOU", url or "—")

    # Exit 0 = sucesso
    sys.exit(0)


if __name__ == "__main__":
    main()
