"""
scripts/ciclo_3h.py — Ciclo de 3 em 3 horas para Source Of e POD Kombucha.

  1. Sync com o dia de hoje (parcial): Meta Ads e Google Ads (ontem + hoje), pedidos Shopify (2 dias).
  2. Saúde dos dados: sync que falhou, dado atrasado, local × API nos 3 últimos dias fechados.
  3. Plano semanal (cenário otimizado das planilhas de planejamento, scripts/metas_planilha.py):
     semana corrente até ontem, por plataforma — ROAS, ritmo de investimento, CPC, CPM, taxa de
     conversão e ticket, com tolerância de TOLERANCIA (20%) contra o plano.
  4. Alerta no Slack (SLACK_ALERT_CHANNEL) se algo estiver fora; o mesmo conjunto de problemas
     (ignorando os números) alerta uma vez por dia, problema novo alerta na hora.

Sessões da Shopify continuam no sync diário (ShopifyQL só fecha o dia no dia seguinte).
Exit 1 se houver problema, para a unit aparecer como falha.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ["SYNC_ATE_HOJE"] = "1"  # loaders do Meta/Google passam a ir até hoje (BRT)

from agent.tz import agora_brt, hoje_brt  # noqa: E402
from scripts import cron_shopify_sync, qualidade_dados, sync_gads_diario, sync_meta_diario  # noqa: E402

log = logging.getLogger("ciclo_3h")
CLIENTES = ("source_of", "pod_kombucha")
DADOS = BASE_DIR / "data" / "performance"
TOLERANCIA = 0.20  # desvio aceito contra o plano antes de alertar
PLATAFORMAS = {"meta_ads": "Meta", "google_ads": "Google"}
BR = str.maketrans(",.", ".,")  # 1,234.50 -> 1.234,50


def _sincronizar(cfg: dict) -> list[str]:
    problemas = []
    shopify = {c["key"]: c for c in cron_shopify_sync.CLIENTES_SHOPIFY}
    for cli in CLIENTES:
        etapas = [("Meta", lambda: sync_meta_diario.sync_cliente(cli, cfg[cli], 2)),
                  ("Google Ads", lambda: sync_gads_diario.sync_cliente(cli, cfg[cli], 2)),
                  ("Shopify", lambda: cron_shopify_sync.sync_cliente(shopify[cli], 2, False))]
        for nome, rodar in etapas:
            try:
                ok = rodar()
            except Exception as e:
                ok, nome = False, f"{nome} ({str(e)[:100]})"
            if ok is False:
                problemas.append(f"{cli}: sync {nome} falhou")
    return problemas


def _soma(db: Path, sql: str, *args) -> float:
    if not db.exists():
        return 0.0
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        try:
            return float(c.execute(sql, args).fetchone()[0] or 0)
        except sqlite3.OperationalError:
            return 0.0


def _max_data(db: Path, tabela: str) -> str:
    if not db.exists():
        return ""
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        try:
            return c.execute(f"SELECT MAX(data) FROM {tabela}").fetchone()[0] or ""
        except sqlite3.OperationalError:
            return ""


def _tabelas(cli: str, cfg: dict) -> list[tuple[str, Path, str]]:
    p = cfg[cli]["plataformas"]
    return [("Meta", DADOS / cli / "meta_ads.db", p["meta_ads"]["tabela_db"]),
            ("Google Ads", DADOS / cli / "google_ads.db", p["google_ads"]["tabela_db"]),
            ("Shopify", DADOS / cli / "shopify_receita.db", "shopify_receita_diaria")]


def _frescor(cfg: dict) -> list[str]:
    ontem = (hoje_brt() - timedelta(days=1)).isoformat()
    return [f"{cli}: {nome} parado em {ult or 'vazio'} (esperado ≥ {ontem})"
            for cli in CLIENTES for nome, db, tab in _tabelas(cli, cfg)
            if (ult := _max_data(db, tab)) < ontem]


def _real(cli: str, plat: str, ini: date, fim: date) -> dict:
    from db.connections import query_fed_one
    return query_fed_one("""
        SELECT SUM(investimento) AS inv, SUM(impressoes) AS imp, SUM(cliques) AS cli,
               SUM(conversoes) AS conv, SUM(receita) AS rec
        FROM v_performance WHERE cliente_key = ? AND plataforma = ? AND data BETWEEN ? AND ?
    """, (cli, plat, ini.isoformat(), fim.isoformat())) or {}


def _div(a, b):
    return a / b if a is not None and b else None


def comparar_plano(rotulo: str, plano: dict, real: dict, dias: int, tol: float = TOLERANCIA) -> list[str]:
    """Desvios da semana até agora contra o plano semanal. real: inv, imp, cli, conv, rec (somas)."""
    g = {k: float(real.get(k) or 0) for k in ("inv", "imp", "cli", "conv", "rec")}
    obs = {  # métrica: (valor real, valor do plano, maior é pior?)
        "Investimento": (g["inv"], plano.get("invest", 0) * dias / 7, None),
        "ROAS": (_div(g["rec"], g["inv"]), plano.get("roas"), False),
        "CPC": (_div(g["inv"], g["cli"]), plano.get("cpc"), True),
        "CPM": (_div(g["inv"] * 1000, g["imp"]), plano.get("cpm"), True),
        "Conversão": (_div(g["conv"], g["cli"]), plano.get("conv"), False),
        "Ticket": (_div(g["rec"], g["conv"]), plano.get("ticket"), False),
    }
    out = []
    for nome, (v, p, maior_pior) in obs.items():
        if v is None or not p:
            continue
        desvio = v / p - 1
        fora = abs(desvio) > tol if maior_pior is None else (desvio > tol if maior_pior else desvio < -tol)
        if fora:
            molde = "{:.2%}" if nome == "Conversão" else "{:.2f}" if nome == "ROAS" else "R$ {:,.2f}"
            fmt = lambda x: molde.format(x).translate(BR)  # noqa: E731
            extra = " (ritmo esperado até ontem)" if nome == "Investimento" else ""
            out.append(f"{rotulo}: {nome} {fmt(v)} × plano {fmt(p)}{extra} ({desvio:+.0%})")
    return out


def _plano_semana(cfg: dict) -> list[str]:
    """Semana corrente até ontem (na segunda, a semana anterior inteira) contra o plano das planilhas."""
    from scripts import metas_planilha
    fim = hoje_brt() - timedelta(days=1)
    ini = fim - timedelta(days=fim.weekday())
    dias = (fim - ini).days + 1
    sem = (ini - metas_planilha.S40).days // 7 + 40
    out = []
    for cli in CLIENTES:
        try:
            plano = metas_planilha.plano(cli)
        except Exception as e:
            out.append(f"{cli}: planilha de metas indisponível ({str(e)[:100]})")
            continue
        for plat, nome in PLATAFORMAS.items():
            if not cfg[cli]["plataformas"].get(plat, {}).get("ativo"):
                continue
            p = plano.get(plat, {}).get(ini)
            if not p:
                out.append(f"{cli}/{nome}: plano da S{sem} não encontrado na planilha")
                continue
            out += comparar_plano(f"{cli}/{nome} S{sem} ({dias}d)", p, _real(cli, plat, ini, fim), dias)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cfg = json.loads((BASE_DIR / "clientes.json").read_text())["clientes"]
    hoje = hoje_brt()

    tecnicos = _sincronizar(cfg) + _frescor(cfg)
    tecnicos += qualidade_dados.verificar(hoje - timedelta(days=3), hoje - timedelta(days=1), set(CLIENTES))
    problemas = tecnicos + _plano_semana(cfg)

    if not problemas:
        log.info("✅ ciclo 3h: dados em dia, conferem com a fonte e dentro das metas")
        return 0
    texto = (f":rotating_light: *Ciclo 3h* ({agora_brt():%d/%m %H:%M}): {len(problemas)} ponto(s) fora do esperado\n• "
             + "\n• ".join(problemas[:25]) + ("\n…" if len(problemas) > 25 else ""))
    log.error(texto)
    # números fora da chave: o mesmo desvio com valor atualizado não realerta no mesmo dia
    chave = hashlib.sha1("\n".join(sorted(re.sub(r"[\d.,%+-]+", "#", p) for p in problemas)).encode()).hexdigest()[:12]
    qualidade_dados._alertar(texto, f"ciclo_3h:{hoje}:{chave}")
    # exit 1 só para falha técnica: desvio do plano é negócio (já alertado) e não deve pintar o post de saúde
    return 1 if tecnicos else 0


if __name__ == "__main__":
    _p = {"invest": 700, "roas": 5, "cpc": 3, "cpm": 50, "conv": 0.05, "ticket": 300}
    _ok = {"inv": 300, "imp": 6000, "cli": 100, "conv": 5, "rec": 1500}  # 3 dias, tudo no plano
    assert comparar_plano("x", _p, _ok, 3) == [], comparar_plano("x", _p, _ok, 3)
    _ruim = dict(_ok, inv=500)  # gasto 67% acima do ritmo, ROAS 3 (−40%), CPC e CPM 67% mais caros
    _r = comparar_plano("x", _p, _ruim, 3)
    assert [l.split(":")[1].split()[0] for l in _r] == ["Investimento", "ROAS", "CPC", "CPM"], _r
    assert comparar_plano("x", {"invest": 700}, {}, 3) == ["x: Investimento R$ 0,00 × plano R$ 300,00 (ritmo esperado até ontem) (-100%)"]
    sys.exit(main())
