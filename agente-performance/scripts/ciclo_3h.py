"""
scripts/ciclo_3h.py — Ciclo de 3 em 3 horas para Source Of e POD Kombucha.

  1. Sync com o dia de hoje (parcial): Meta Ads e Google Ads (ontem + hoje), pedidos Shopify (2 dias).
  2. Saúde dos dados: sync que falhou, dado atrasado, local × API nos 3 últimos dias fechados.
  3. Metas de performance (clientes.json → "metas" do cliente; sem metas, pula).
  4. Alerta no Slack (SLACK_ALERT_CHANNEL) se algo estiver fora; o mesmo conjunto de problemas
     alerta uma vez por dia (livro-razão de idempotência), problema novo alerta na hora.

Sessões da Shopify continuam no sync diário (ShopifyQL só fecha o dia no dia seguinte).
Exit 1 se houver problema, para a unit aparecer como falha.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ["SYNC_ATE_HOJE"] = "1"  # loaders do Meta/Google passam a ir até hoje (BRT)

from agent.tz import agora_brt, hoje_brt  # noqa: E402
from scripts import cron_shopify_sync, qualidade_dados, sync_gads_diario, sync_meta_diario  # noqa: E402

log = logging.getLogger("ciclo_3h")
CLIENTES = ("source_of", "pod_kombucha")
DADOS = BASE_DIR / "data" / "performance"
HORA_MIN_ROAS_HOJE = 12  # antes do meio-dia o ROAS parcial do dia é ruído


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


def _dia(cli: str, cfg: dict, dia: str) -> tuple[float, float]:
    """(investimento Meta+Google, receita Shopify) do dia."""
    t = _tabelas(cli, cfg)
    inv = sum(_soma(db, f"SELECT SUM(investimento) FROM {tab} WHERE data = ?", dia) for _, db, tab in t[:2])
    return inv, _soma(t[2][1], "SELECT receita FROM shopify_receita_diaria WHERE data = ?", dia)


def avaliar_metas(cli: str, metas: dict, hoje: tuple[float, float], ontem: tuple[float, float], hora: int) -> list[str]:
    """Regras de performance. metas: roas_min (receita Shopify ÷ investimento Meta+Google), invest_max_dia, invest_min_dia."""
    out = []
    (inv_h, rec_h), (inv_o, rec_o) = hoje, ontem
    if "invest_max_dia" in metas and inv_h > metas["invest_max_dia"]:
        out.append(f"{cli}: investimento de hoje R$ {inv_h:,.2f} acima do teto R$ {metas['invest_max_dia']:,.2f}")
    if "invest_min_dia" in metas and inv_o < metas["invest_min_dia"]:
        out.append(f"{cli}: investimento de ontem R$ {inv_o:,.2f} abaixo do mínimo R$ {metas['invest_min_dia']:,.2f}")
    if "roas_min" in metas:
        if inv_o and rec_o / inv_o < metas["roas_min"]:
            out.append(f"{cli}: ROAS de ontem {rec_o / inv_o:.2f} abaixo da meta {metas['roas_min']:.2f}")
        if hora >= HORA_MIN_ROAS_HOJE and inv_h and rec_h / inv_h < metas["roas_min"]:
            out.append(f"{cli}: ROAS de hoje até agora {rec_h / inv_h:.2f} abaixo da meta {metas['roas_min']:.2f}")
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cfg = json.loads((BASE_DIR / "clientes.json").read_text())["clientes"]
    hoje = hoje_brt()

    problemas = _sincronizar(cfg) + _frescor(cfg)
    problemas += qualidade_dados.verificar(hoje - timedelta(days=3), hoje - timedelta(days=1), set(CLIENTES))
    for cli in CLIENTES:
        if metas := cfg[cli].get("metas"):
            problemas += avaliar_metas(cli, metas, _dia(cli, cfg, hoje.isoformat()),
                                       _dia(cli, cfg, (hoje - timedelta(days=1)).isoformat()), agora_brt().hour)

    if not problemas:
        log.info("✅ ciclo 3h: dados em dia, conferem com a fonte e dentro das metas")
        return 0
    texto = (f":rotating_light: *Ciclo 3h* ({agora_brt():%d/%m %H:%M}): {len(problemas)} ponto(s) fora do esperado\n• "
             + "\n• ".join(problemas[:25]) + ("\n…" if len(problemas) > 25 else ""))
    log.error(texto)
    chave = hashlib.sha1("\n".join(sorted(problemas)).encode()).hexdigest()[:12]
    qualidade_dados._alertar(texto, f"ciclo_3h:{hoje}:{chave}")
    return 1


if __name__ == "__main__":
    assert avaliar_metas("x", {"roas_min": 3}, (100, 200), (100, 400), 13) == ["x: ROAS de hoje até agora 2.00 abaixo da meta 3.00"]
    assert avaliar_metas("x", {"roas_min": 3}, (100, 200), (100, 400), 9) == []
    assert len(avaliar_metas("x", {"invest_max_dia": 50, "invest_min_dia": 200}, (60, 0), (100, 0), 9)) == 2
    sys.exit(main())
