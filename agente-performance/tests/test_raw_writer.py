"""db/raw_writer: payload guardado como veio, hash estável, falha nunca derruba o sync."""
import json

from db import raw_writer


class _Job:
    def result(self):
        return None


class _ClienteFalso:
    def __init__(self):
        self.carregado = None
        self.queries = []

    def create_table(self, *a, **k):
        return None

    def load_table_from_json(self, registros, staging, job_config=None):
        self.carregado = registros
        return _Job()

    def query(self, sql):
        self.queries.append(sql)
        return _Job()

    def delete_table(self, *a, **k):
        return None


def test_payload_vai_inteiro_e_hash_depende_do_conteudo():
    c = _ClienteFalso()
    payload = {"id": 7, "status": {"data": {"alias": "paid"}}, "valor": 10.5}
    n = raw_writer.salvar_raw("raw_teste", ["loja", "order_id"],
                              [{"loja": "poddi", "order_id": 7, "payload": payload}], client=c)
    assert n == 1
    reg = c.carregado[0]
    assert json.loads(reg["payload_json"]) == payload
    assert reg["order_id"] == "7"
    assert reg["payload_hash"] == raw_writer._hash(reg["payload_json"])
    assert raw_writer._hash('{"a": 1}') != raw_writer._hash('{"a": 2}')


def test_merge_so_atualiza_quando_o_hash_muda():
    c = _ClienteFalso()
    raw_writer.salvar_raw("raw_teste", ["loja", "order_id"],
                          [{"loja": "x", "order_id": 1, "payload": {}}], client=c)
    sql = c.queries[0]
    assert "WHEN MATCHED AND T.payload_hash != S.payload_hash" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql


def test_falha_do_bigquery_nao_levanta():
    class _Quebrado(_ClienteFalso):
        def load_table_from_json(self, *a, **k):
            raise RuntimeError("bq fora do ar")

    assert raw_writer.salvar_raw("raw_teste", ["a"], [{"a": 1, "payload": {}}], client=_Quebrado()) == 0


def test_sem_linhas_nao_faz_nada():
    assert raw_writer.salvar_raw("raw_teste", ["a"], [], client=None) == 0
