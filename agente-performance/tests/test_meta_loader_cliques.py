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
