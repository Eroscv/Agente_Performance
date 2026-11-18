"""
scripts/saude.py — Post diário de saúde dos agentes em #alertas-tecnicos (SLACK_ALERT_CHANNEL).

Uma mensagem por dia (13:30 UTC, depois do relatório das 13h), verde ou vermelha:
  • rotinas: última execução de cada timer guara-* (systemd) — falhou = problema
  • dados: última data em cada fonte (Meta, Google, Shopify) — atrás de ontem = problema
  • teste-ouro: é a rotina "qualidade_dados" (banco e relatórios × API); falha aparece nas rotinas
  • VM: RAM disponível < 300 MB, swap em uso > 300 MB ou disco livre < 2 GB = problema
  • RPTN (Shopify → Reportana): serviço parado, nenhum ciclo em 8 h ou cliente em status='erro' = problema

Silêncio também é sinal: se o post não chegar, a própria VM/agenda caiu.

Uso:  python scripts/saude.py            # posta
      python scripts/saude.py --ver      # só imprime
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

RAM_MIN_MB = 300
SWAP_MAX_MB = 300
DISCO_MIN_GB = 2
RPTN = Path("/home/guara/reportana_api_shopify")
RPTN_CICLO_MAX_H = 8   # intervalo adaptativo vai até 4 h; 2 ciclos sem log = parado


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


def rptn(raiz: Path = RPTN, ativo: str | None = None, agora: float | None = None) -> list[str]:
    """Problemas do RPTN (AUD-13). Ele não é timer, então não aparece nas rotinas."""
    if not raiz.exists():
        return []
    ativo = ativo or subprocess.run(["systemctl", "is-active", "guara-rptn"], capture_output=True, text=True).stdout.strip()
    if ativo != "active":
        return [f"RPTN: serviço guara-rptn {ativo or 'desconhecido'}"]
    out = []
    log = raiz / "reports" / "sync_loop.log"
    horas = ((agora or time.time()) - log.stat().st_mtime) / 3600 if log.exists() else None
    if horas is None or horas > RPTN_CICLO_MAX_H:
        out.append("RPTN: nenhum ciclo registrado " + (f"há {horas:.0f} h" if horas else "(sem log)"))
    try:
        # immutable=1: entre ciclos o RPTN fecha o banco e o -shm some; sob ProtectSystem=strict o modo ro
        # sozinho falharia ("readonly database"). Para uma contagem, ler sem o WAL basta.
        with sqlite3.connect(f"file:{raiz / 'leads_reportana.db'}?mode=ro&immutable=1", uri=True) as c:
            tabelas = [t for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'seg!_%' ESCAPE '!'")]
            for t in tabelas:
                if not {"status", "erro"} <= {col[1] for col in c.execute(f"PRAGMA table_info({t})")}:
                    continue  # seg_last_edit e afins não são tabelas de segmento
                n, motivo = c.execute(f"SELECT COUNT(*), MAX(erro) FROM {t} WHERE status='erro'").fetchone()
                if n:
                    out.append(f"RPTN: {n} cliente(s) presos em erro em {t} (ex.: {' '.join(str(motivo).split())[:80]})")
    except sqlite3.Error as e:
        out.append(f"RPTN: banco ilegível ({e})")
    return out


def _swap_mb() -> int:
    m = {l.split(":")[0]: int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("Swap")}
    return (m.get("SwapTotal", 0) - m.get("SwapFree", 0)) // 1024


def montar(dia: str, rot: list[dict], frescor: list[str], ram_mb: int, disco_gb: float,
           swap_mb: int = 0, outros: list[str] = ()) -> tuple[str, list[str]]:
    """Texto do post e lista de problemas (vazia = verde)."""
    problemas = [f"rotina *{r['nome']}* falhou (código {r['ExecMainStatus']}) em {_quando(r['ExecMainExitTimestamp'])}"
                 for r in rot if r["rodou"] and r["Result"] not in ("success", "")]
    problemas += [f"dados: {f}" for f in frescor]
    if ram_mb < RAM_MIN_MB:
        problemas.append(f"VM com pouca RAM disponível: {ram_mb} MB (mínimo {RAM_MIN_MB})")
    if swap_mb > SWAP_MAX_MB:
        problemas.append(f"VM usando swap: {swap_mb} MB (máximo {SWAP_MAX_MB}); falta memória para algum serviço")
    if disco_gb < DISCO_MIN_GB:
        problemas.append(f"VM com pouco disco livre: {disco_gb:.1f} GB (mínimo {DISCO_MIN_GB})")
    problemas += list(outros)

    ok = sum(1 for r in rot if r["rodou"] and r["Result"] == "success")
    nunca = [r["nome"] for r in rot if not r["rodou"]]
    ouro = next((r for r in rot if r["nome"] == "qualidade_dados"), None)
    linhas = [
        f"• Rotinas: {ok} de {len(rot)} ok" + (f" · ainda não rodaram: {', '.join(nunca)}" if nunca else ""),
        "• Dados de anúncios e Shopify: " + ("em dia (até ontem)" if not frescor else f"{len(frescor)} fonte(s) atrasada(s)"),
        "• Teste-ouro (banco e relatórios × API): " + ("sem registro" if not (ouro and ouro["rodou"])
                                                      else "ok" if ouro["Result"] == "success" else "*divergência*"),
        f"• VM: RAM disponível {ram_mb} MB · swap {swap_mb} MB · disco livre {disco_gb:.0f} GB",
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
                      _ram_mb(), shutil.disk_usage("/").free / 1e9, _swap_mb(), rptn())
    print(texto)
    if not a.ver:
        qualidade_dados._alertar(texto, f"saude:{hoje.isoformat()}")
    return 0  # o post é o produto; vermelho não é falha desta rotina


if __name__ == "__main__":
    sys.exit(main())
