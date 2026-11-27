"""
Paridade entre os dois caminhos de leitura do agente: BigQuery (views de consumo do gold,
correlacao.v_agente_*) e banco local (DuckDB sobre os .db de data/performance).

Roda as consultas do agente nos dois caminhos e compara investimento, receita e compras.
Precisa de rede, da credencial do BigQuery e dos .db locais, então só roda com
RODAR_PARIDADE_BQ=1:

    RODAR_PARIDADE_BQ=1 python -m pytest -q tests/test_paridade_bigquery.py

PARIDADE_DATA_DIR aponta para outra pasta data/ (com performance/*.db), se preciso.

Janela fixa de dias fechados em que as duas fontes têm a mesma carga. Cliques e CTR ficam de
fora: a camada tratada corrige dias em que o loader antigo da Meta gravou todos os cliques
como cliques no link, e o banco local não.
"""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RODAR_PARIDADE_BQ") != "1",
    reason="paridade BigQuery x local: rode com RODAR_PARIDADE_BQ=1 (rede + credencial + .db locais)",
)

@pytest.fixture(autouse=True, scope="module")
def _ambiente():
    """Credencial do .env e, se PARIDADE_DATA_DIR estiver definido, os .db locais de lá."""
    from dotenv import load_dotenv
    import db.duck as duck
    load_dotenv(Path(__file__).parent.parent / ".env")
    dados = os.environ.get("PARIDADE_DATA_DIR")
    antes = dict(duck._SQLITE_DBS)
    if dados:
        duck._SQLITE_DBS = {k: Path(dados) / Path(v).relative_to(duck.DATA_DIR) for k, v in antes.items()}
        duck._duck_conn = None
    yield
    duck._SQLITE_DBS = antes
    duck._duck_conn = None


FIM = (date.today() - timedelta(days=2)).isoformat()
INICIO = (date.today() - timedelta(days=7)).isoformat()
CLIENTES = ["source_of", "pod_kombucha"]
CAMPOS = ("investimento", "receita", "compras", "investimento_total", "receita_total", "compras_total")


def _nos_dois(fn, **kw):
    resultados = {}
    for backend in ("bq", "duck"):
        os.environ["QUERY_FED_BACKEND"] = backend
        try:
            resultados[backend] = fn(**kw)
        finally:
            os.environ.pop("QUERY_FED_BACKEND", None)
    return resultados["bq"], resultados["duck"]


def _numeros(linha: dict) -> dict:
    return {k: round(float(v or 0), 2) for k, v in linha.items() if k in CAMPOS}


@pytest.mark.parametrize("cliente", CLIENTES)
def test_resumo_periodo(cliente):
    from agent.queries.resumo import resumo_periodo
    bq, local = _nos_dois(resumo_periodo, data_inicio=INICIO, data_fim=FIM, cliente_key=cliente)
    assert _numeros(bq) == _numeros(local)
    assert _numeros(bq), "nenhum número comparado"


@pytest.mark.parametrize("cliente", CLIENTES)
def test_distribuicao_investimento(cliente):
    from agent.queries.resumo import distribuicao_investimento
    bq, local = _nos_dois(distribuicao_investimento, data_inicio=INICIO, data_fim=FIM, cliente_key=cliente)
    assert {r["campanha"]: _numeros(r) for r in bq} == {r["campanha"]: _numeros(r) for r in local}


@pytest.mark.parametrize("cliente", CLIENTES)
def test_top_campanhas_roas(cliente):
    from agent.queries.ranking import top_campanhas_roas
    bq, local = _nos_dois(top_campanhas_roas, limit=10, data_inicio=INICIO, data_fim=FIM, cliente_key=cliente)
    assert [(r["campanha"], _numeros(r)) for r in bq] == [(r["campanha"], _numeros(r)) for r in local]


@pytest.mark.parametrize("cliente", CLIENTES)
def test_evolucao_diaria(cliente):
    from agent.queries.evolucao import evolucao_diaria
    bq, local = _nos_dois(evolucao_diaria, data_inicio=INICIO, data_fim=FIM, cliente_key=cliente)
    assert {r["data"][:10]: _numeros(r) for r in bq} == {r["data"][:10]: _numeros(r) for r in local}


@pytest.mark.parametrize("cliente", CLIENTES)
def test_criativos(cliente):
    from agent.queries.criativos import top_criativos_roas
    bq, local = _nos_dois(top_criativos_roas, data_inicio=INICIO, data_fim=FIM, cliente_key=cliente, limit=10)
    assert [(r["ad_id"], _numeros(r)) for r in bq] == [(r["ad_id"], _numeros(r)) for r in local]


def test_erro_no_bigquery_nao_vira_lista_vazia(monkeypatch):
    """Consulta inválida no caminho só-BigQuery tem que falhar, não devolver []."""
    from db.connections import query_fed
    monkeypatch.setenv("QUERY_FED_BACKEND", "bq")
    with pytest.raises(Exception):
        query_fed("SELECT coluna_que_nao_existe FROM campanhas_unificada")
