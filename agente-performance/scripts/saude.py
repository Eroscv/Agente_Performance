"""
scripts/saude.py — Post diário de saúde dos agentes em #alertas-tecnicos (SLACK_ALERT_CHANNEL).

Uma mensagem por dia (13:30 UTC, depois do relatório das 13h), verde ou vermelha:
  • rotinas: última execução de cada timer guara-* (systemd) — falhou = problema
  • dados: última data em cada fonte (Meta, Google, Shopify) — atrás de ontem = problema
  • teste-ouro: é a rotina "qualidade_dados" (banco e relatórios × API); falha aparece nas rotinas
  • VM: RAM disponível < 300 MB ou disco livre < 2 GB = problema

Silêncio também é sinal: se o post não chegar, a própria VM/agenda caiu.

Uso:  python scripts/saude.py            # posta
      python scripts/saude.py --ver      # só imprime
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

RAM_MIN_MB = 300
DISCO_MIN_GB = 2


def _show(units: list[str], props: str) -> list[dict]:
    out = subprocess.run(["systemctl", "show", *units, "-p", props], capture_output=True, text=True, timeout=30).stdout
    return [dict(l.split("=", 1) for l in bloco.splitlines() if "=" in l) for bloco in out.strip().split("\n\n") if bloco]


def _nome(unit: str) -> str:
    """guara-job@relatorio_diario.service -> relatorio_diario; guara-transcricao.service -> transcricao"""
    return unit.removesuffix(".service").split("@", 1)[-1].removeprefix("guara-")


def _quando(ts: str) -> str:
    """'Fri 2026-11-13 13:00:35 UTC' -> '13/11 13:00 UTC'"""
    p = ts.split()
    return f"{p[1][8:10]}/{p[1][5:7]} {p[2][:5]} UTC" if len(p) >= 3 else ts


def rotinas() -> list[dict]:
    timers = [t for t in _show(["guara-*.timer"], "Id,Unit,ActiveState,LastTriggerUSec") if t.get("ActiveState") == "active"]
    svc = {s["Id"]: s for s in _show([t["Unit"] for t in timers], "Id,Result,ExecMainStatus,ExecMainExitTimestamp")} if timers else {}
    return [{"nome": _nome(t["Unit"]), "rodou": bool(t.get("LastTriggerUSec")),
             **{k: svc.get(t["Unit"], {}).get(k, "") for k in ("Result", "ExecMainStatus", "ExecMainExitTimestamp")}}
            for t in timers]


def montar(dia: str, rot: list[dict], frescor: list[str], ram_mb: int, disco_gb: float) -> tuple[str, list[str]]:
    """Texto do post e lista de problemas (vazia = verde)."""
    problemas = [f"rotina *{r['nome']}* falhou (código {r['ExecMainStatus']}) em {_quando(r['ExecMainExitTimestamp'])}"
                 for r in rot if r["rodou"] and r["Result"] not in ("success", "")]
    problemas += [f"dados: {f}" for f in frescor]
    if ram_mb < RAM_MIN_MB:
        problemas.append(f"VM com pouca RAM disponível: {ram_mb} MB (mínimo {RAM_MIN_MB})")
    if disco_gb < DISCO_MIN_GB:
        problemas.append(f"VM com pouco disco livre: {disco_gb:.1f} GB (mínimo {DISCO_MIN_GB})")

    ok = sum(1 for r in rot if r["rodou"] and r["Result"] == "success")
    nunca = [r["nome"] for r in rot if not r["rodou"]]
    ouro = next((r for r in rot if r["nome"] == "qualidade_dados"), None)
    linhas = [
        f"• Rotinas: {ok} de {len(rot)} ok" + (f" · ainda não rodaram: {', '.join(nunca)}" if nunca else ""),
        "• Dados de anúncios e Shopify: " + ("em dia (até ontem)" if not frescor else f"{len(frescor)} fonte(s) atrasada(s)"),
        "• Teste-ouro (banco e relatórios × API): " + ("sem registro" if not (ouro and ouro["rodou"])
                                                      else "ok" if ouro["Result"] == "success" else "*divergência*"),
        f"• VM: RAM disponível {ram_mb} MB · disco livre {disco_gb:.0f} GB",
    ]
    cab = (f":large_green_circle: *Saúde dos agentes — {dia}*: tudo ok" if not problemas
           else f":red_circle: *Saúde dos agentes — {dia}*: {len(problemas)} problema(s)\n" + "\n".join(f"• {p}" for p in problemas))
    return cab + "\n" + "\n".join(linhas), problemas


def _ram_mb() -> int:
    for l in Path("/proc/meminfo").read_text().splitlines():
        if l.startswith("MemAvailable:"):
            return int(l.split()[1]) // 1024
    return 0


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    from agent.tz import hoje_brt
    from scripts import ciclo_3h, qualidade_dados

    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", action="store_true", help="só imprime, não posta")
    a = ap.parse_args()
    hoje = hoje_brt()
    cfg = json.loads((BASE_DIR / "clientes.json").read_text())["clientes"]
    texto, _ = montar(hoje.strftime("%d/%m"), rotinas(), ciclo_3h._frescor(cfg),
                      _ram_mb(), shutil.disk_usage("/").free / 1e9)
    print(texto)
    if not a.ver:
        qualidade_dados._alertar(texto, f"saude:{hoje.isoformat()}")
    return 0  # o post é o produto; vermelho não é falha desta rotina


if __name__ == "__main__":
    sys.exit(main())
