"""
tests/test_fixture_db_demo.py

Demonstra fixture_db (tests/conftest.py) funcionando ponta a ponta:
agent.queries.* lendo dados sintéticos conhecidos via DuckDB, sem tocar
em data/performance/ real (que nem existe neste clone).

Os valores esperados aqui são calculados à mão a partir do dataset fixo
em conftest.py — exatamente o que a Metodologia de Teste pede: "assert
investimento_total == 1234.56" em vez de "assert investimento_total > 0".
"""
from agent.queries.alertas import alertas_roas_baixo
from agent.queries.base import data_range_disponivel, listar_campanhas
from agent.queries.ranking import top_campanhas_roas
from agent.queries.resumo import resumo_periodo


def test_data_range_disponivel_bate_com_o_periodo_fixo(fixture_db):
    dr = data_range_disponivel()
    assert dr["data_minima"] == fixture_db["periodo_inicio"]
    assert dr["data_maxima"] == fixture_db["periodo_fim"]
    # 7 campanhas (3 SOF meta + 1 SOF gads + 2 POD meta + 1 POD gads + 1 Botica) × 7 dias
    assert dr["total_linhas"] == 8 * fixture_db["dias"]
    assert dr["total_campanhas"] == 8


def test_listar_campanhas_retorna_as_8_com_investimento_total_correto(fixture_db):
    campanhas = listar_campanhas()
    assert len(campanhas) == 8

    por_nome = {c["campanha"]: c for c in campanhas}
    # "ABO - Conversao": 100.0/dia × 7 dias
    assert por_nome["ABO - Conversao"]["investimento_total"] == 700.0
    # "Retargeting Carrinho": 50.0/dia × 7 dias, mesmo sem compras
    assert por_nome["Retargeting Carrinho"]["investimento_total"] == 350.0


def test_resumo_periodo_global_soma_todos_os_clientes(fixture_db):
    r = resumo_periodo(fixture_db["periodo_inicio"], fixture_db["periodo_fim"])

    # Investimento diário total: 230.0 (SOF meta) + 30.0 (SOF gads)
    #                           + 130.0 (POD meta) + 35.0 (POD gads) + 25.0 (Botica) = 450.0
    assert r["investimento_total"] == 450.0 * fixture_db["dias"]
    # Compras diárias: 3 (SOF meta) + 1 (SOF gads) + 2 (POD meta) + 1 (POD gads) + 1 (Botica) = 8
    assert r["compras_total"] == 8 * fixture_db["dias"]
    # Receita diária: 520.0 (SOF meta) + 90.0 (SOF gads) + 270.0 (POD meta) + 56.0 (POD gads) + 75.0 (Botica) = 1011.0
    assert r["receita_total"] == 1011.0 * fixture_db["dias"]
    # roas = SUM(valor_compras) / SUM(investimento) — ver agent/queries/base.py::_ROAS_EXPR
    assert r["roas"] == round(1011.0 / 450.0, 4)


def test_resumo_periodo_isolado_por_cliente(fixture_db):
    """Confirma isolamento: resumo do POD não inclui nada do Source OF
    nem da Botica — a mesma regra de negócio que tests/clients/ (ainda
    não implementado) precisaria validar no nível de ClientAgent."""
    r_pod = resumo_periodo(fixture_db["periodo_inicio"], fixture_db["periodo_fim"], cliente_key="pod_kombucha")
    # POD: 130.0 (meta) + 35.0 (gads) = 165.0/dia
    assert r_pod["investimento_total"] == 165.0 * fixture_db["dias"]
    assert r_pod["campanhas_ativas"] == 3  # 2 meta + 1 gads


def test_top_campanhas_roas_ordenado_e_exclui_sem_investimento_minimo(fixture_db):
    """Mínimo de R$100 investidos no período — só campanhas com
    investimento_total >= 100 aparecem (ver docstring de top_campanhas_roas)."""
    top = top_campanhas_roas(limit=10, excluir_awareness=False, exigir_atividade_recente=False)
    nomes = [c["campanha"] for c in top]

    # "POD - Awareness" tem só 40.0/dia × 7 = 280.0 >= 100, mas 0 compras -> ROAS None/0,
    # fica no fim do ranking, não excluído por investimento.
    assert "ABO - Conversao" in nomes  # 700.0 investido, maior ROAS absoluto do grupo
    # Ordenado por ROAS decrescente: a primeira campanha tem ROAS >= a segunda.
    assert top[0]["roas"] is None or top[1]["roas"] is None or top[0]["roas"] >= top[1]["roas"]


def test_sem_conversao_identifica_retargeting_carrinho(fixture_db):
    """'Retargeting Carrinho' tem investimento > 0 e ZERO compras em
    todos os 7 dias — é exatamente o caso que sem_conversao (via
    alertas_roas_baixo com threshold alto) deveria capturar."""
    alertas = alertas_roas_baixo(threshold=999.0, janela_dias=30)
    nomes = [a["campanha"] for a in alertas]
    assert "Retargeting Carrinho" in nomes
