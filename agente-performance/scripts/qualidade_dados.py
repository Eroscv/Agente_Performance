"""
scripts/qualidade_dados.py — Conferência dos dados locais contra a fonte.

Para cada conta ativa (Meta e Google Ads), nos últimos N dias fechados:
  1. Reconciliação: investimento por dia no SQLite × total da própria API no nível da conta.
     Pega dia faltando, duplicação e filtro errado no sync (ex.: campanha removida sumindo).
  2. Duplicidade: linhas repetidas pelo grão da tabela (deveria ser sempre zero; o UNIQUE garante).
  3. Teste-ouro da leitura: investimento por dia na view v_performance (o que relatórios, alertas e bot
     leem) × soma da API das contas do cliente. Pega erro na camada de leitura com o SQLite certo
     (ex.: campanha duplicada por anúncio na view, nov/2026).

Saída: tabela no log; se houver divergência, alerta no Slack (SLACK_ALERT_CHANNEL), uma vez por dia
por conjunto de problemas, e exit 1 para a unit aparecer como falha.

Roda todo dia às 07:30 UTC (depois dos syncs, antes do relatório das 13h).

Uso:
  python scripts/qualidade_dados.py            # últimos 7 dias
  python scripts/qualidade_dados.py --dias 30
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("qualidade")

TOLERANCIA_REL = 0.01   # 1%: atribuição tardia da Meta mexe em centavos
TOLERANCIA_ABS = 1.00   # R$ 1
BR = str.maketrans(",.", ".,")  # 1,234.50 -> 1.234,50
GRAOS = {"meta_ads": ("account_id", "data", "ad_id"), "google_ads": ("data", "campaign_id")}


def _local(db: Path, tabela: str, ini: str, fim: str, filtro_conta: str | None) -> dict[str, float]:
    if not db.exists():
        return {}
    sql = f"SELECT data, SUM(investimento) FROM {tabela} WHERE data BETWEEN ? AND ?"
    args: list = [ini, fim]
    if filtro_conta:
        sql += " AND REPLACE(COALESCE(account_id, ''), 'act_', '') = ?"
        args.append(filtro_conta)
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        try:
            return {d: v or 0.0 for d, v in c.execute(sql + " GROUP BY data", args)}
        except sqlite3.OperationalError:
            return {}


def _duplicatas(db: Path, tabela: str, grao: tuple) -> int:
    if not db.exists():
        return 0
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        try:
            g = ", ".join(grao)
            return c.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM {tabela} GROUP BY {g} HAVING COUNT(*) > 1)").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def _view(cliente: str, plat: str, ini: str, fim: str) -> dict[str, float]:
    """Investimento por dia como os relatórios enxergam (DuckDB sobre todos os SQLite)."""
    from db.connections import query_fed
    rows = query_fed("SELECT CAST(data AS VARCHAR) AS d, SUM(investimento) AS v FROM v_performance "
                     "WHERE cliente_key = ? AND plataforma = ? AND data BETWEEN ? AND ? GROUP BY 1",
                     [cliente, plat, ini, fim])
    return {r["d"]: float(r["v"] or 0) for r in rows}


def _brl(v: float) -> str:
    return f"R$ {v:,.2f}".translate(BR)


def _api_meta(conta: str, token_env: str, ini: str, fim: str) -> dict[str, float]:
    r = requests.get(f"https://graph.facebook.com/v20.0/act_{conta}/insights", timeout=60, params={
        "access_token": os.environ[token_env], "level": "account", "fields": "spend",
        "time_increment": 1, "time_range": json.dumps({"since": ini, "until": fim}), "limit": 100})
    r.raise_for_status()
    return {x["date_start"]: float(x.get("spend") or 0) for x in r.json().get("data", [])}


def _api_gads(customer_id: str, ini: str, fim: str) -> dict[str, float]:
    from google_ads.client import get_client
    svc = get_client().get_service("GoogleAdsService")
    q = (f"SELECT segments.date, metrics.cost_micros FROM customer "
         f"WHERE segments.date BETWEEN '{ini}' AND '{fim}'")
    out: dict[str, float] = {}
    for lote in svc.search_stream(customer_id=customer_id, query=q):
        for row in lote.results:
            out[row.segments.date] = out.get(row.segments.date, 0.0) + row.metrics.cost_micros / 1e6
    return out


def comparar(local: dict[str, float], api: dict[str, float], ini: date, fim: date) -> list[tuple]:
    """Dias divergentes: (dia, local, api). Dia ausente nos dois lados conta como zero."""
    ruins = []
    d = ini
    while d <= fim:
        k = d.isoformat()
        lo, ap = round(local.get(k, 0.0), 2), round(api.get(k, 0.0), 2)
        if abs(lo - ap) > max(TOLERANCIA_ABS, TOLERANCIA_REL * ap):
            ruins.append((k, lo, ap))
        d += timedelta(days=1)
    return ruins


def contas() -> list[dict]:
    from db.connections import PERFORMANCE_DBS
    cfg = json.loads((BASE_DIR / "clientes.json").read_text())["clientes"]
    out = []
    for cli, c in cfg.items():
        if not c.get("ativo"):
            continue
        m = c.get("plataformas", {}).get("meta_ads", {})
        if m.get("ativo") and m.get("meta_account_id"):
            tk = m.get("meta_token_env", "META_ACCESS_TOKEN")
            for chave in ("meta_account_id", "meta_account_id2"):
                if m.get(chave):
                    out.append({"cliente": cli, "plat": "meta_ads", "conta": m[chave].removeprefix("act_"),
                                "token": m.get("meta_token_env2", tk) if chave.endswith("2") else tk,
                                "db": PERFORMANCE_DBS.get((cli, "meta_ads")), "tabela": m.get("tabela_db")})
        g = c.get("plataformas", {}).get("google_ads", {})
        if g.get("ativo") and g.get("gads_customer_id"):
            out.append({"cliente": cli, "plat": "google_ads", "conta": g["gads_customer_id"],
                        "db": PERFORMANCE_DBS.get((cli, "google_ads")), "tabela": g.get("tabela_db")})
    return out


def _alertar(texto: str, chave: str) -> None:
    token, canal = os.getenv("SLACK_BOT_TOKEN"), os.getenv("SLACK_ALERT_CHANNEL") or os.getenv("SLACK_CHANNEL")
    if not (token and canal):
        log.warning("alerta não enviado ao Slack (SLACK_BOT_TOKEN/SLACK_ALERT_CHANNEL vazios)")
        return
    from utils.idempotencia import uma_vez
    with uma_vez(chave) as pode:
        if pode:
            r = requests.post("https://slack.com/api/chat.postMessage", timeout=20,
                              headers={"Authorization": f"Bearer {token}"}, json={"channel": canal, "text": texto})
            if not r.ok or not r.json().get("ok"):
                raise RuntimeError(f"Slack recusou o alerta: {r.text[:200]}")


def verificar(ini: date, fim: date, clientes: set[str] | None = None) -> list[str]:
    """Divergências local × API (e grãos duplicados) no período; lista vazia = tudo confere."""
    problemas: list[str] = []
    api_grupo: dict[tuple, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    sem_api: set[tuple] = set()
    for c in contas():
        if clientes and c["cliente"] not in clientes:
            continue
        rotulo = f"{c['cliente']}/{c['plat']}/{c['conta']}"
        if not (c["db"] and c["tabela"]):
            problemas.append(f"{rotulo}: sem banco/tabela configurados")
            continue
        try:
            api = (_api_meta(c["conta"], c["token"], ini.isoformat(), fim.isoformat()) if c["plat"] == "meta_ads"
                   else _api_gads(c["conta"], ini.isoformat(), fim.isoformat()))
        except Exception as e:
            problemas.append(f"{rotulo}: API indisponível ({str(e)[:120]})")
            sem_api.add((c["cliente"], c["plat"]))
            continue
        for d, v in api.items():
            api_grupo[(c["cliente"], c["plat"])][d] += v
        local = _local(Path(c["db"]), c["tabela"], ini.isoformat(), fim.isoformat(),
                       c["conta"] if c["plat"] == "meta_ads" else None)
        ruins = comparar(local, api, ini, fim)
        dup = _duplicatas(Path(c["db"]), c["tabela"], GRAOS[c["plat"]])
        log.info("%-45s local R$ %12.2f | API R$ %12.2f | dias divergentes %d | grãos duplicados %d",
                 rotulo, sum(local.values()), sum(api.values()), len(ruins), dup)
        for d, lo, apv in ruins:
            problemas.append(f"{rotulo} {d}: banco {_brl(lo)} × API {_brl(apv)}")
        if dup:
            problemas.append(f"{rotulo}: {dup} grão(s) duplicado(s)")

    for (cli, plat), api in api_grupo.items():
        if (cli, plat) in sem_api:  # soma parcial da API daria falso alarme; o problema já foi listado
            continue
        try:
            vis = _view(cli, plat, ini.isoformat(), fim.isoformat())
        except Exception as e:
            problemas.append(f"{cli}/{plat}: view dos relatórios ilegível ({str(e)[:120]})")
            continue
        ruins = comparar(vis, api, ini, fim)
        log.info("%-45s relatório R$ %12.2f | API R$ %12.2f | dias divergentes %d",
                 f"{cli}/{plat} (v_performance)", sum(vis.values()), sum(api.values()), len(ruins))
        for d, lo, apv in ruins:
            problemas.append(f"{cli}/{plat} {d}: relatório mostra {_brl(lo)} × API {_brl(apv)}")
    return problemas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=7)
    a = ap.parse_args()
    fim = date.today() - timedelta(days=1)
    ini = fim - timedelta(days=a.dias - 1)
    problemas = verificar(ini, fim)
    if not problemas:
        log.info("✅ dados conferem com a fonte (%s → %s)", ini, fim)
        return 0
    texto = (f":warning: *Qualidade de dados* ({ini} → {fim}): {len(problemas)} divergência(s)\n• "
             + "\n• ".join(problemas[:25]) + ("\n…" if len(problemas) > 25 else ""))
    log.error(texto)
    _alertar(texto, f"qualidade_dados:{fim}:{hashlib.sha1(chr(10).join(problemas).encode()).hexdigest()[:12]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
