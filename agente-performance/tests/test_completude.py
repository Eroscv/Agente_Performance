"""Sync do Meta: dia que falha não some em silêncio."""
import sqlite3
from datetime import date, timedelta

import pytest


def test_dia_abortado_grava_o_resto_e_falha(tmp_path, monkeypatch):
    import meta_ads.loader as L
    ruim = date.today() - timedelta(days=2)
    monkeypatch.setenv("META_ACCESS_TOKEN", "x")
    monkeypatch.setattr(L.time, "sleep", lambda s: None)

    def periodo(conta, ini, fim, token_env):
        if ini == ruim:
            raise RuntimeError("HTTP 500")
        return [{"data": ini.isoformat(), "ad_id": "a1", "account_id": "1", "campanha": "c", "investimento": 10.0}]
    monkeypatch.setattr(L, "buscar_insights_periodo", periodo)
    monkeypatch.setattr(L, "_enriquecer_status", lambda rows, *a, **k: rows)
    alertas = []
    import db.sync_alerts as A
    monkeypatch.setattr(A, "registrar_resultado", lambda *a, **k: alertas.append(k.get("sucesso")))

    db = tmp_path / "m.db"
    with pytest.raises(L.SyncIncompleto) as e:
        L.carregar_meta("act_1", dias=3, append=True, tabela="t", db_path=db)
    assert e.value.dias == [ruim]
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2   # os 2 dias bons ficaram
    assert alertas == [False]                                             # sem "sucesso" zerando a contagem


def test_reconciliacao_aponta_so_o_que_diverge():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from qualidade_dados import comparar
    ini, fim = date(2026, 11, 1), date(2026, 11, 4)
    local = {"2026-11-01": 100.0, "2026-11-02": 100.4, "2026-11-04": 200.0}   # 03 faltando, 04 duplicado
    api = {"2026-11-01": 100.0, "2026-11-02": 100.0, "2026-11-03": 80.0, "2026-11-04": 100.0}
    assert [d for d, *_ in comparar(local, api, ini, fim)] == ["2026-11-03", "2026-11-04"]
