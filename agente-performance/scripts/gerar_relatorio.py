"""
scripts/gerar_relatorio.py

CLI para gerar e publicar o relatório Meta Ads no Notion.

Uso:
    python scripts/gerar_relatorio.py --cliente source_of --periodo semana
    python scripts/gerar_relatorio.py --cliente source_of --inicio 2026-06-16 --fim 2026-06-22
    python scripts/gerar_relatorio.py --cliente source_of --periodo semana --dry-run
"""

import argparse
import logging
import sys
import os
from datetime import date, timedelta
from pathlib import Path

# Garante que o projeto está no path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

CLIENTES_DISPONIVEIS = [
    "source_of",
    "consorcio_sorteado",
    "maximo_servico",
    "be_mushin",
    "bazar_precatorio",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Agente de Performance Guará — Gerador de Relatórios Meta Ads",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  # Relatório da semana passada (padrão)
  python scripts/gerar_relatorio.py --cliente source_of --periodo semana

  # Período customizado
  python scripts/gerar_relatorio.py --cliente source_of --inicio 2026-06-16 --fim 2026-06-22

  # Testar sem publicar no Notion
  python scripts/gerar_relatorio.py --cliente source_of --periodo semana --dry-run
        """,
    )
    parser.add_argument(
        "--cliente", "-c",
        default="source_of",
        choices=CLIENTES_DISPONIVEIS,
        help="Cliente a gerar relatório (default: source_of)",
    )
    parser.add_argument(
        "--periodo", "-p",
        choices=["semana", "mes", "hoje", "ontem"],
        default="semana",
        help="Período pré-definido (default: semana)",
    )
    parser.add_argument(
        "--inicio",
        type=str,
        default=None,
        help="Data de início no formato YYYY-MM-DD (sobrescreve --periodo)",
    )
    parser.add_argument(
        "--fim",
        type=str,
        default=None,
        help="Data de fim no formato YYYY-MM-DD (sobrescreve --periodo)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gera o relatório mas NÃO publica no Notion",
    )
    return parser.parse_args()


def resolver_periodo(periodo: str, inicio_str: str = None, fim_str: str = None):
    """Resolve datas de início e fim a partir dos argumentos."""
    if inicio_str and fim_str:
        return date.fromisoformat(inicio_str), date.fromisoformat(fim_str)

    hoje = date.today()

    if periodo == "hoje":
        return hoje, hoje
    elif periodo == "ontem":
        ontem = hoje - timedelta(days=1)
        return ontem, ontem
    elif periodo == "semana":
        # Semana anterior: segunda → domingo
        seg = hoje - timedelta(days=hoje.weekday() + 7)
        dom = seg + timedelta(days=6)
        return seg, dom
    elif periodo == "mes":
        # Mês anterior
        primeiro_este = hoje.replace(day=1)
        ultimo_mes    = primeiro_este - timedelta(days=1)
        primeiro_mes  = ultimo_mes.replace(day=1)
        return primeiro_mes, ultimo_mes
    else:
        # Semana atual (fallback)
        seg = hoje - timedelta(days=hoje.weekday())
        return seg, hoje


def main():
    args = parse_args()

    data_inicio, data_fim = resolver_periodo(args.periodo, args.inicio, args.fim)
    publicar = not args.dry_run

    print("\n" + "═" * 60)
    print("  ⚙️  Agente de Performance Guará")
    print("  📊  Gerador de Relatório Meta Ads")
    print("═" * 60)
    print(f"  Cliente:   {args.cliente}")
    print(f"  Período:   {data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}")
    print(f"  Publicar:  {'NÃO (dry-run)' if args.dry_run else 'SIM → Notion'}")
    print("═" * 60 + "\n")

    from relatorio.builder import gerar_relatorio_semanal

    resultado = gerar_relatorio_semanal(
        cliente=args.cliente,
        data_inicio=data_inicio,
        data_fim=data_fim,
        publicar=publicar,
    )

    if "erro" in resultado:
        print(f"\n❌ Erro: {resultado['erro']}")
        sys.exit(1)

    print("\n✅ Relatório gerado com sucesso!\n")
    print(f"  📅 Período:        {resultado.get('periodo')}")
    print(f"  📊 Campanhas:      {resultado.get('num_campanhas')}")

    r = resultado.get("resumo", {})
    invest = r.get("total_invest")
    roas   = r.get("roas")
    compras = r.get("total_compras")

    if invest is not None:
        invest_fmt = f"R$ {float(invest):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        print(f"  💰 Investimento:   {invest_fmt}")
    if roas is not None:
        print(f"  📈 ROAS:           {float(roas):.2f}x")
    if compras is not None:
        print(f"  🛒 Compras:        {int(compras)}")

    alertas = resultado.get("alertas", [])
    if alertas:
        print(f"\n  🚨 Alertas ({len(alertas)}):")
        for a in alertas:
            print(f"     {a}")

    if publicar and resultado.get("url"):
        print(f"\n  🔗 Notion: {resultado['url']}")
    elif publicar and resultado.get("page_id"):
        pid = resultado["page_id"].replace("-", "")
        print(f"\n  🔗 Notion: https://notion.so/{pid}")
    elif args.dry_run:
        print(f"\n  ℹ️  Dry-run: relatório gerado em memória, não publicado.")

    print("\n" + "═" * 60 + "\n")


if __name__ == "__main__":
    main()
