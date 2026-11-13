"""Sem MONGODB_URI (Mongo desativado, URI fora do código): dedup só pelo SQLite, sem pular reunião nova."""
import sqlite3

import sync_reunioes as s


def test_sem_uri_nao_pula_reuniao_nova(monkeypatch):
    monkeypatch.setattr(s, "MONGO_URI", "")
    monkeypatch.delenv("TRANSCRICAO_SEM_MONGO", raising=False)
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE reunioes (id INTEGER PRIMARY KEY, fonte_id TEXT)")
    c.execute("INSERT INTO reunioes (fonte_id) VALUES ('velho')")
    assert s.get_mongo_col() is None and s.salvar_mongo({}, "x", "novo") is False
    assert s.ja_processado(c, "novo") is False and s.ja_processado(c, "velho") is True
    assert "mongodb" not in s.MONGO_URI
