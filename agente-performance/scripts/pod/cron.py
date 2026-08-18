"""
scripts/pod/cron.py

Cron de relatórios e alertas exclusivo do POD Kombucha.
Chama o core em scripts/cron_todos_relatorios.py com cliente fixo.

Uso:
    python scripts/pod/cron.py --periodo diario
    python scripts/pod/cron.py --periodo semana
    python scripts/pod/cron.py --periodo mes
    python scripts/pod/cron.py --apenas-alertas
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import argparse
import logging
from datetime import date, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CLIENTE_KEY = "pod_kombucha"


def main():
    parser = argparse.ArgumentParser(description="Cron POD Kombucha")
    parser.add_argument("--periodo",       default="diario",
                        choices=["diario", "semana", "mes"],
                        help="Período do relatório")
    parser.add_argument("--data",          default=None, help="YYYY-MM-DD ref (padrão: ontem)")
    parser.add_argument("--apenas-alertas",action="store_true",
                        help="Só verifica alertas ROAS, sem gerar relatório")
    parser.add_argument("--publicar",      action="store_true", default=True)
    args = parser.parse_args()

    data_ref = args.data or (date.today() - timedelta(days=1)).isoformat()
    d_ref    = date.fromisoformat(data_ref)

    if args.periodo == "diario":
        data_inicio = data_fim = d_ref
    elif args.periodo == "semana":
        # Segunda a domingo da semana passada
        data_fim    = d_ref - timedelta(days=d_ref.weekday() + 1)
        data_inicio = data_fim - timedelta(days=6)
    else:  # mes
        from calendar import monthrange
        primeiro = d_ref.replace(day=1)
        if primeiro.month == 1:
            ant = primeiro.replace(year=primeiro.year - 1, month=12)
        else:
            ant = primeiro.replace(month=primeiro.month - 1)
        data_inicio = ant
        data_fim    = ant.replace(day=monthrange(ant.year, ant.month)[1])

    logger.info("POD Kombucha · período=%s · %s → %s · alertas=%s",
                args.periodo, data_inicio, data_fim, args.apenas_alertas)

    from scripts.cron_todos_relatorios import executar_cliente, executar_cliente_multi_plataforma
    from db.registry import get_cliente

    cfg = get_cliente(CLIENTE_KEY)
    plataformas_ativas = [p for p, pc in cfg.get("plataformas", {}).items() if pc.get("ativo")]

    if len(plataformas_ativas) > 1:
        result = executar_cliente_multi_plataforma(
            cfg=cfg,
            cliente_key=CLIENTE_KEY,
            data_inicio=data_inicio,
            data_fim=data_fim,
            periodo=args.periodo,
            publicar=args.publicar,
            apenas_alertas=args.apenas_alertas,
        )
    else:
        result = executar_cliente(
            cfg=cfg,
            cliente_key=CLIENTE_KEY,
            data_inicio=data_inicio,
            data_fim=data_fim,
            periodo=args.periodo,
            publicar=args.publicar,
            apenas_alertas=args.apenas_alertas,
        )

    logger.info("Resultado: %s", result)


if __name__ == "__main__":
    main()
