"""Tradução do SQL do agente (views locais) para as views de consumo do gold no BigQuery.

Testes puros, sem rede: só conferem o texto traduzido.
"""
from db.bq_translator import translate

VIEW = "`guara-db.correlacao.v_agente_campanhas`"


def test_troca_a_view_e_os_parametros():
    sql, params = translate(
        "SELECT campanha FROM campanhas_unificada WHERE cliente_key = ? AND data_inicio >= ? LIMIT ?",
        ("source_of", "2026-11-01", 5),
    )
    assert VIEW in sql
    assert "@p0" in sql and "@p1" in sql and "@p2" in sql and "?" not in sql
    assert params == ["source_of", "2026-11-01", 5]


def test_nao_renomeia_colunas_nem_apelidos():
    sql, _ = translate(
        "SELECT SUM(compras) AS conversoes, MIN(data_inicio) AS data_inicio, "
        "SUM(valor_compras) AS receita, AVG(ctr_link) AS ctr FROM campanhas_unificada WHERE cliente_key = ?",
        ("pod_kombucha",),
    )
    for trecho in ("AS conversoes", "AS data_inicio", "valor_compras", "ctr_link", "cliente_key"):
        assert trecho in sql


def test_having_com_apelido_igual_a_coluna_aponta_para_a_view():
    # ROUND(SUM(investimento)) AS investimento + HAVING SUM(investimento): no BigQuery o nome
    # dentro do HAVING viraria o apelido ("Aggregations of aggregations are not allowed").
    sql, _ = translate(
        """
        SELECT campanha, ROUND(SUM(investimento), 2) AS investimento
        FROM campanhas_unificada
        WHERE cliente_key = ?
        GROUP BY campanha
        HAVING SUM(investimento) >= 50
           AND (SUM(COALESCE(compras, 0)) = 0 OR SUM(COALESCE(valor_compras, 0)) = 0)
        ORDER BY investimento DESC
        """,
        ("source_of",),
    )
    assert f"FROM {VIEW} AS _v" in sql
    assert "HAVING SUM(_v.investimento) >= 50" in sql
    assert "SUM(COALESCE(_v.compras, 0)) = 0 OR SUM(COALESCE(_v.valor_compras, 0)) = 0" in sql
    assert "ROUND(SUM(investimento), 2) AS investimento" in sql      # SELECT fica como está
    assert "ORDER BY investimento DESC" in sql                       # apelido solto fica como está


def test_order_by_com_agregacao_e_case():
    sql, _ = translate(
        """
        SELECT campanha, ROUND(SUM(COALESCE(valor_compras, 0)), 2) AS receita,
               CASE WHEN SUM(valor_compras) = 0 THEN 'sem_conversao' ELSE 'ok' END AS tipo_alerta
        FROM campanhas_unificada
        GROUP BY campanha
        HAVING SUM(investimento) >= 100 AND (SUM(valor_compras) = 0 OR SUM(valor_compras) / NULLIF(SUM(investimento), 0) < ?)
        ORDER BY CASE WHEN SUM(valor_compras) = 0 THEN 0 ELSE 1 END ASC,
                 SUM(valor_compras) / NULLIF(SUM(investimento), 0) ASC
        """,
        (1.0,),
    )
    assert "tipo_alerta = 0" not in sql                              # defeito da versão antiga
    assert "HAVING SUM(_v.investimento) >= 100" in sql
    assert "ORDER BY CASE WHEN SUM(_v.valor_compras) = 0 THEN 0 ELSE 1 END ASC" in sql
    assert "SUM(_v.valor_compras) / NULLIF(SUM(_v.investimento), 0) ASC" in sql


def test_consulta_sem_having_nem_order_by_agregado_nao_ganha_apelido():
    sql, _ = translate("SELECT MIN(data_inicio) AS de, MAX(data_fim) AS ate FROM campanhas_unificada", ())
    assert " AS _v" not in sql


def test_order_by_de_janela_dentro_de_subconsulta_fica_intacto():
    sql, _ = translate(
        "SELECT x FROM (SELECT campanha AS x, RANK() OVER (ORDER BY SUM(investimento) DESC) AS r "
        "FROM v_performance GROUP BY campanha) t ORDER BY x",
        (),
    )
    assert "ORDER BY SUM(investimento) DESC" in sql
    assert "_v." not in sql
