"""Cliques do loader da Meta: `clicks` = todos os cliques, `inline_link_clicks` = cliques no link."""
from meta_ads.loader import FIELDS, _normalizar


def test_fields_pede_cliques_no_link():
    assert "inline_link_clicks" in FIELDS.split(",")


def test_cliques_todos_nunca_menor_que_cliques_link():
    row = {"date_start": "2026-11-05", "clicks": "120", "inline_link_clicks": "80",
           "impressions": "1000", "spend": "10"}
    r = _normalizar(row)
    assert r["cliques_link"] == 80.0
    assert r["cliques_todos"] == 120.0
    assert r["cliques_todos"] >= r["cliques_link"]


def test_buscar_insights_falha_quando_todos_os_dias_dao_erro(monkeypatch):
    """Token inválido não pode virar '0 linhas, sucesso': o sync tem que falhar."""
    import pytest
    from meta_ads import loader

    monkeypatch.setenv("META_ACCESS_TOKEN", "x")
    monkeypatch.setattr(loader.time, "sleep", lambda *_: None)

    def _erro(*a, **k):
        raise RuntimeError("Meta API error: Bad signature")

    monkeypatch.setattr(loader, "buscar_insights_periodo", _erro)
    with pytest.raises(RuntimeError, match="todos os"):
        loader.buscar_insights("act_1", dias=2)


def test_buscar_insights_devolve_o_que_deu_certo_se_so_um_dia_falha(monkeypatch):
    from meta_ads import loader

    monkeypatch.setenv("META_ACCESS_TOKEN", "x")
    monkeypatch.setattr(loader.time, "sleep", lambda *_: None)
    chamadas = {"n": 0}

    def _um_dia_falha(account_id, ini, fim, token_env="META_ACCESS_TOKEN"):
        chamadas["n"] += 1
        if chamadas["n"] <= 3:          # 1º dia: as 3 tentativas falham
            raise RuntimeError("500")
        return [{"ad_id": "1"}]

    monkeypatch.setattr(loader, "buscar_insights_periodo", _um_dia_falha)
    assert loader.buscar_insights("act_1", dias=2) == [{"ad_id": "1"}]
