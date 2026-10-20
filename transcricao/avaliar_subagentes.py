#!/usr/bin/env python3
"""
avaliar_subagentes.py

Taxa de erro/sucesso (com IC de Wilson) de cada subagente do pipeline de
Transcrição, numa janela de dias — implementa o pedido "métricas pra
avaliar os subagentes individualmente" feito na sessão de estruturação
em agents/.

Cada camada é avaliada pela fonte de dados que já existe pra ela (ver
agents/*.py::metricas() de cada classe):

  IngestaoAgent    — sync_log (já existia antes desta sessão)
  ParserAgent      — campo vazio em `reunioes` (parsear_reuniao nunca
                      lança exceção; "erro" aqui é qualidade de extração)
  AnaliseAgent      — log_subagente (tabela nova, criada nesta sessão —
  ComunicacaoAgent    só existe em runs futuros; roda zerada até lá)
  TicketAgent      — delega a MetricasAgent.coletar() (mesma tabela,
                      mesmo cálculo — não duplica)
  MetricasAgent    — é a própria camada de métrica; roda zerada até
                      alguém decidir o que "avaliar o avaliador" significa

Uso:
  python3 avaliar_subagentes.py                 # janela padrão: 90 dias
  python3 avaliar_subagentes.py --janela 30
  python3 avaliar_subagentes.py --json
"""
import argparse
import json
import sqlite3
from pathlib import Path

from agents import PIPELINE

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "reunioes.db"


def avaliar(janela_dias: int = 90) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    resultado = {}
    for classe in PIPELINE:
        agente = classe()
        try:
            m = agente.metricas(conn, janela_dias=janela_dias)
        except Exception as e:
            m = {"erro_execucao": str(e)}
        resultado[classe.__name__] = {"camada": agente.CAMADA, **m}

    conn.close()
    return resultado


def formatar(resultado: dict, janela_dias: int) -> str:
    linhas = [
        "=" * 72,
        f"MÉTRICAS POR SUBAGENTE — Pipeline de Transcrição (últimos {janela_dias} dias)",
        "=" * 72,
        "",
    ]
    for nome, m in resultado.items():
        linhas.append(f"[{m.get('camada', '?')}] {nome}")

        if "erro_execucao" in m:
            linhas.append(f"  ⚠️  falha ao calcular métrica: {m['erro_execucao']}")
        elif "taxas" in m:
            # TicketAgent delega a MetricasAgent.coletar() — formato por tipo de alerta.
            if not m["taxas"]:
                linhas.append("  (sem tickets na janela)")
            for tipo, t in m["taxas"].items():
                aviso = f" — {t['aviso_ic']}" if t.get("aviso_ic") else ""
                linhas.append(
                    f"  {tipo}: {t['resolvidos']}/{t['total']} resolvidos "
                    f"({t['taxa_pct']}%, IC 95%: {t['ic_low_pct']}–{t['ic_high_pct']}%){aviso}"
                )
            if m.get("sla_vencidos"):
                linhas.append(f"  🚨 {len(m['sla_vencidos'])} ticket(s) com SLA vencido")
        else:
            n = m.get("n", 0)
            taxa = m.get("taxa_erro", 0) * 100
            ic_low = m.get("ic_wilson_low", 0) * 100
            ic_high = m.get("ic_wilson_high", 0) * 100
            confiavel = "" if n >= 20 else "  ⚠️ n<20 — IC amplo, não confiável ainda"
            linhas.append(
                f"  ok={m.get('ok', 0)} erro={m.get('erro', 0)} (n={n}) | "
                f"taxa de erro: {taxa:.1f}% (IC 95%: {ic_low:.1f}–{ic_high:.1f}%){confiavel}"
            )
        linhas.append("")
    return "\n".join(linhas)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--janela", type=int, default=90, help="Janela em dias (padrão: 90)")
    parser.add_argument("--json", action="store_true", help="Saída JSON")
    args = parser.parse_args()

    resultado = avaliar(args.janela)

    if args.json:
        print(json.dumps(resultado, indent=2, ensure_ascii=False, default=str))
    else:
        print(formatar(resultado, args.janela))


if __name__ == "__main__":
    main()
