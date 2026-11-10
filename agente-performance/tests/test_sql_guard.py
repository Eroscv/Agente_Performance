"""Guarda do Text-to-SQL: só SELECT, só o cliente da conversa (offline)."""
import pytest

pytest.importorskip("anthropic")
from agent.sql_generator import _validate_sql

T = "`guara-db.guara_performance.ads_diario`"


@pytest.mark.parametrize("sql", [
    f"SELECT * FROM {T} WHERE cliente = 'pod_kombucha'",                 # outro cliente
    f"SELECT * FROM {T} WHERE cliente IN ('source_of', 'pod_kombucha')",
    f"SELECT * FROM {T} WHERE cliente = 'source_of' AND x = 1 OR 1 = 1",  # precedência
    f"SELECT 1; DROP TABLE {T}",
    f"SELECT * FROM {T} WHERE cliente = cliente",
])
def test_recusa(sql):
    assert _validate_sql(sql, "source_of")[1]


def test_aceita_e_injeta_cliente_e_limit():
    sql, err = _validate_sql(f"SELECT campanha FROM {T} WHERE data >= '2026-11-01' GROUP BY campanha", "source_of")
    assert err is None and "cliente = 'source_of'" in sql and "LIMIT 50" in sql
