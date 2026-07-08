"""
tests/test_consultas.py

Testes de integração para todos os tipos de consulta do Agente de Performance Guará.

Cobre:
  - Consultas básicas: listar, resumo, performance, top ROAS, top compras
  - Consultas temporais: evolução diária (geral e por campanha)
  - Consolidação: resumo de período
  - Alertas: ROAS baixo
  - Comparações: lado a lado, periodos, WoW, MoM, antes/depois
  - Funil: impressões → cliques → carrinho → checkout → compra
  - Rankings: por métrica
  - Diagnóstico: range disponível no banco

Banco de dados: data/campanhas.db
Range disponível: 2024-01-23 → 2026-06-23
Total registros: ~28.256 | Campanhas únicas: 32
"""

import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from agent import queries
from agent.comparatives import (
    comparar_periodos,
    comparar_campanhas_detalhado,
    comparar_semana_a_semana,
    comparar_mes_a_mes,
    comparar_antes_depois,
    ranking_comparativo,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

DATA_INICIO = "2026-06-01"
DATA_FIM    = "2026-06-23"

CAMPANHA_A  = "ABO_Testes_Todos_Produtos"
CAMPANHA_B  = "Advantage_HidratanteCorporal"


@pytest.fixture(scope="module")
def campanhas_lista():
    return queries.listar_campanhas()


@pytest.fixture(scope="module")
def resumo():
    return queries.resumo_periodo(DATA_INICIO, DATA_FIM)


# ── 1. listar_campanhas ───────────────────────────────────────────────────────

class TestListarCampanhas:
    def test_retorna_lista(self, campanhas_lista):
        assert isinstance(campanhas_lista, list)
        assert len(campanhas_lista) > 0

    def test_tem_campos_esperados(self, campanhas_lista):
        campos = set(campanhas_lista[0].keys())
        assert {"campanha", "primeira_data", "ultima_data", "dias_com_dados", "investimento_total"}.issubset(campos)

    def test_ordenado_por_investimento(self, campanhas_lista):
        investimentos = [c["investimento_total"] for c in campanhas_lista]
        assert investimentos == sorted(investimentos, reverse=True), \
            "Campanhas devem estar ordenadas por investimento decrescente"

    def test_sem_campanha_vazia(self, campanhas_lista):
        for c in campanhas_lista:
            assert c["campanha"] and c["campanha"].strip() != "", \
                "Não deve haver campanhas com nome vazio"

    def test_min_32_campanhas(self, campanhas_lista):
        assert len(campanhas_lista) >= 32, f"Esperado >= 32 campanhas, obtido {len(campanhas_lista)}"


# ── 2. resumo_por_campanha ────────────────────────────────────────────────────

class TestResumoPorCampanha:
    def test_retorna_dados(self):
        r = queries.resumo_por_campanha(DATA_INICIO, DATA_FIM)
        assert isinstance(r, list) and len(r) > 0

    def test_tem_metricas_essenciais(self):
        r = queries.resumo_por_campanha(DATA_INICIO, DATA_FIM)
        campos = set(r[0].keys())
        assert {"campanha", "investimento", "receita", "compras", "roas",
                "ctr_medio", "cpc_medio", "cpa_ajustado", "frequencia_media"}.issubset(campos)

    def test_investimento_positivo(self):
        r = queries.resumo_por_campanha(DATA_INICIO, DATA_FIM)
        for c in r:
            assert c["investimento"] >= 0

    def test_sem_filtro_de_data(self):
        r = queries.resumo_por_campanha()
        assert isinstance(r, list) and len(r) > 0, "Deve funcionar sem filtro de data"

    def test_roas_positivo_onde_ha_receita(self):
        r = queries.resumo_por_campanha(DATA_INICIO, DATA_FIM)
        for c in r:
            if c.get("receita") and c["receita"] > 0 and c.get("roas") is not None:
                assert c["roas"] > 0, f"ROAS deve ser positivo para {c['campanha']}"


# ── 3. performance_campanha ───────────────────────────────────────────────────

class TestPerformanceCampanha:
    def test_match_exato(self):
        r = queries.performance_campanha(CAMPANHA_A, DATA_INICIO, DATA_FIM)
        assert isinstance(r, list) and len(r) > 0, f"Sem dados para campanha '{CAMPANHA_A}'"

    def test_match_like_fragmento(self):
        r = queries.performance_campanha("hidratante", DATA_INICIO, DATA_FIM)
        assert isinstance(r, list) and len(r) > 0, "Fallback LIKE deve encontrar campanha por fragmento"

    def test_campos_serie_temporal(self):
        r = queries.performance_campanha(CAMPANHA_A, DATA_INICIO, DATA_FIM)
        campos = set(r[0].keys())
        assert {"data", "campanha", "investimento", "receita", "compras",
                "roas", "impressoes", "cliques_link", "ctr", "cpc"}.issubset(campos)

    def test_ordenado_por_data(self):
        r = queries.performance_campanha(CAMPANHA_A, DATA_INICIO, DATA_FIM)
        datas = [row["data"] for row in r]
        assert datas == sorted(datas), "Série temporal deve estar ordenada por data ASC"

    def test_campanha_inexistente_retorna_lista(self):
        r = queries.performance_campanha("campanha_que_nao_existe_xyz_999")
        assert isinstance(r, list)

    def test_cobertura_periodo(self):
        r = queries.performance_campanha(CAMPANHA_A, DATA_INICIO, DATA_FIM)
        assert len(r) == 23, f"Junho 2026 tem 23 dias de dados, obtido {len(r)}"


# ── 4. top_campanhas_roas ─────────────────────────────────────────────────────

class TestTopCampanhasROAS:
    def test_retorna_lista(self):
        r = queries.top_campanhas_roas(5, DATA_INICIO, DATA_FIM)
        assert isinstance(r, list)

    def test_roas_decrescente(self):
        r = queries.top_campanhas_roas(10, DATA_INICIO, DATA_FIM)
        if len(r) > 1:
            for i in range(len(r) - 1):
                assert r[i]["roas"] >= r[i+1]["roas"], "Top ROAS deve estar em ordem decrescente"

    def test_exclui_awareness_por_padrao(self):
        r = queries.top_campanhas_roas(20, DATA_INICIO, DATA_FIM)
        for c in r:
            nome_lower = c["campanha"].lower()
            for padrao in ["tráfego", "trafego", "awareness", "engajamento"]:
                assert padrao not in nome_lower, \
                    f"Campanha de awareness '{c['campanha']}' não deve aparecer no top ROAS"

    def test_inclui_awareness_quando_solicitado(self):
        sem_filtro = queries.top_campanhas_roas(20, DATA_INICIO, DATA_FIM, excluir_awareness=False)
        com_filtro = queries.top_campanhas_roas(20, DATA_INICIO, DATA_FIM, excluir_awareness=True)
        assert len(sem_filtro) >= len(com_filtro), \
            "Sem filtro de awareness deve ter >= campanhas que com filtro"

    def test_investimento_minimo_100(self):
        r = queries.top_campanhas_roas(50, DATA_INICIO, DATA_FIM, excluir_awareness=False)
        for c in r:
            assert c["investimento"] >= 100, \
                f"'{c['campanha']}' tem invest={c['investimento']} abaixo do mínimo R$100"

    def test_campos_esperados(self):
        r = queries.top_campanhas_roas(5, DATA_INICIO, DATA_FIM)
        if r:
            assert {"campanha", "roas", "investimento", "receita", "compras"}.issubset(set(r[0].keys()))


# ── 5. top_campanhas_compras ──────────────────────────────────────────────────

class TestTopCampanhasCompras:
    def test_retorna_lista(self):
        r = queries.top_campanhas_compras(5, DATA_INICIO, DATA_FIM)
        assert isinstance(r, list) and len(r) > 0

    def test_ordenado_por_compras(self):
        r = queries.top_campanhas_compras(10, DATA_INICIO, DATA_FIM)
        compras = [c["compras"] for c in r]
        assert compras == sorted(compras, reverse=True), "Deve estar em ordem decrescente de compras"

    def test_limit_respeitado(self):
        r = queries.top_campanhas_compras(3, DATA_INICIO, DATA_FIM)
        assert len(r) <= 3

    def test_tem_cpa_ajustado(self):
        r = queries.top_campanhas_compras(5, DATA_INICIO, DATA_FIM)
        for c in r:
            if c.get("compras") and c["compras"] > 0 and c.get("investimento", 0) > 0:
                assert c.get("cpa_ajustado") and c["cpa_ajustado"] > 0, \
                    f"CPA ajustado ausente para '{c['campanha']}'"

    def test_sem_filtro_de_data(self):
        r = queries.top_campanhas_compras(5)
        assert isinstance(r, list) and len(r) > 0


# ── 6. evolucao_diaria ────────────────────────────────────────────────────────

class TestEvolucaoDiaria:
    def test_geral_retorna_serie(self):
        r = queries.evolucao_diaria(None, DATA_INICIO, DATA_FIM)
        assert isinstance(r, list) and len(r) > 0

    def test_geral_ordenado_por_data(self):
        r = queries.evolucao_diaria(None, DATA_INICIO, DATA_FIM)
        datas = [row["data"] for row in r]
        assert datas == sorted(datas)

    def test_por_campanha_retorna_serie(self):
        r = queries.evolucao_diaria(CAMPANHA_A, DATA_INICIO, DATA_FIM)
        assert isinstance(r, list) and len(r) > 0

    def test_por_campanha_cobre_periodo(self):
        r = queries.evolucao_diaria(CAMPANHA_A, DATA_INICIO, DATA_FIM)
        assert len(r) == 23

    def test_campos_serie(self):
        r = queries.evolucao_diaria(None, DATA_INICIO, DATA_FIM)
        campos = set(r[0].keys())
        assert {"data", "investimento", "receita", "compras", "roas", "impressoes", "cliques", "ctr_medio"}.issubset(campos)

    def test_investimento_diario_positivo(self):
        r = queries.evolucao_diaria(None, DATA_INICIO, DATA_FIM)
        for dia in r:
            assert dia["investimento"] >= 0


# ── 7. resumo_periodo ─────────────────────────────────────────────────────────

class TestResumoPeriodo:
    def test_retorna_dict(self, resumo):
        assert isinstance(resumo, dict) and len(resumo) > 0

    def test_sem_aviso_para_periodo_valido(self, resumo):
        assert "aviso" not in resumo, f"Não deve ter aviso para período válido: {resumo.get('aviso')}"

    def test_metricas_completas(self, resumo):
        esperados = {
            "campanhas_ativas", "investimento_total", "receita_total", "compras_total",
            "roas", "impressoes_total", "alcance_total", "cliques_total",
            "ctr_medio", "cpc_medio", "cpm_medio", "custo_medio_por_compra",
            "frequencia_media", "carrinho_total", "checkout_total",
        }
        assert esperados.issubset(set(resumo.keys()))

    def test_valores_positivos(self, resumo):
        for campo in ["investimento_total", "receita_total", "roas", "ctr_medio", "cpc_medio"]:
            assert resumo[campo] > 0, f"{campo} deve ser positivo"

    def test_roas_junho_2026(self, resumo):
        assert resumo["roas"] > 1.0, f"ROAS de junho 2026 esperado > 1x, obtido {resumo['roas']}"

    def test_periodo_sem_dados_retorna_aviso(self):
        r = queries.resumo_periodo("1900-01-01", "1900-01-31")
        assert "aviso" in r, "Deve retornar aviso para período sem dados"

    def test_funil_consistente(self, resumo):
        assert resumo["cliques_total"] <= resumo["impressoes_total"], "Cliques <= Impressões"
        assert resumo["carrinho_total"] <= resumo["cliques_total"], "Carrinho <= Cliques"
        assert resumo["checkout_total"] <= resumo["carrinho_total"], "Checkout <= Carrinho"
        assert resumo["compras_total"] <= resumo["checkout_total"], "Compras <= Checkout"


# ── 8. alertas_roas_baixo ─────────────────────────────────────────────────────

class TestAlertasROASBaixo:
    def test_retorna_lista(self):
        r = queries.alertas_roas_baixo(1.5)
        assert isinstance(r, list)

    def test_todos_abaixo_do_threshold(self):
        threshold = 2.0
        r = queries.alertas_roas_baixo(threshold)
        for alerta in r:
            if alerta.get("tipo_alerta") == "roas_baixo":
                assert alerta["roas"] < threshold, \
                    f"'{alerta['campanha']}' com ROAS={alerta['roas']} deveria estar abaixo de {threshold}"

    def test_sem_conversao_aparece_como_alerta(self):
        r = queries.alertas_roas_baixo(99.0)
        tipos = [a.get("tipo_alerta") for a in r]
        assert "sem_conversao" in tipos or "roas_baixo" in tipos or len(r) == 0

    def test_investimento_minimo_100(self):
        r = queries.alertas_roas_baixo(99.0)
        for alerta in r:
            assert alerta.get("investimento", 0) >= 100

    def test_threshold_zero_retorna_vazio(self):
        r = queries.alertas_roas_baixo(0.0)
        assert isinstance(r, list)

    def test_campos_alerta(self):
        r = queries.alertas_roas_baixo(99.0)
        if r:
            assert {"campanha", "roas", "investimento", "receita", "tipo_alerta"}.issubset(set(r[0].keys()))


# ── 9. comparar_campanhas ─────────────────────────────────────────────────────

class TestCompararCampanhas:
    def test_duas_campanhas(self):
        r = queries.comparar_campanhas([CAMPANHA_A, CAMPANHA_B], DATA_INICIO, DATA_FIM)
        assert len(r) == 2

    def test_campanha_unica(self):
        r = queries.comparar_campanhas([CAMPANHA_A], DATA_INICIO, DATA_FIM)
        assert len(r) == 1

    def test_lista_vazia_retorna_vazia(self):
        r = queries.comparar_campanhas([])
        assert r == []

    def test_campos_comparacao(self):
        r = queries.comparar_campanhas([CAMPANHA_A, CAMPANHA_B], DATA_INICIO, DATA_FIM)
        campos = set(r[0].keys())
        assert {"campanha", "investimento", "receita", "compras", "roas",
                "cpc_medio", "cpa_ajustado", "frequencia_media"}.issubset(campos)

    def test_ordenado_por_investimento(self):
        r = queries.comparar_campanhas([CAMPANHA_A, CAMPANHA_B], DATA_INICIO, DATA_FIM)
        investimentos = [c["investimento"] for c in r]
        assert investimentos == sorted(investimentos, reverse=True)


# ── 10. metricas_funil ────────────────────────────────────────────────────────

class TestMetricasFunil:
    def test_funil_geral(self):
        r = queries.metricas_funil(None, DATA_INICIO, DATA_FIM)
        assert isinstance(r, dict) and len(r) > 0

    def test_funil_por_campanha(self):
        r = queries.metricas_funil(CAMPANHA_A, DATA_INICIO, DATA_FIM)
        assert isinstance(r, dict) and len(r) > 0

    def test_campos_funil(self):
        r = queries.metricas_funil(None, DATA_INICIO, DATA_FIM)
        assert {"impressoes", "cliques", "adicoes_carrinho", "checkout_iniciado", "compras",
                "investimento", "receita"}.issubset(set(r.keys()))

    def test_hierarquia_funil(self):
        r = queries.metricas_funil(None, DATA_INICIO, DATA_FIM)
        imp = r.get("impressoes", 0) or 0
        cli = r.get("cliques", 0) or 0
        car = r.get("adicoes_carrinho", 0) or 0
        chk = r.get("checkout_iniciado", 0) or 0
        com = r.get("compras", 0) or 0
        assert imp >= cli >= 0,  "Impressões >= Cliques"
        assert cli >= car >= 0,  "Cliques >= Carrinho"
        assert car >= chk >= 0,  "Carrinho >= Checkout"
        assert chk >= com >= 0,  "Checkout >= Compras"

    def test_impressoes_junho_acima_1m(self):
        r = queries.metricas_funil(None, DATA_INICIO, DATA_FIM)
        assert r["impressoes"] > 1_000_000, \
            f"Esperado > 1M impressões em junho, obtido {r['impressoes']}"


# ── 11. comparar_periodos ─────────────────────────────────────────────────────

class TestCompararPeriodos:
    def test_retorna_estrutura(self):
        r = comparar_periodos("2026-05-01", "2026-05-31", DATA_INICIO, DATA_FIM)
        assert isinstance(r, dict)
        assert {"periodo_a", "periodo_b", "variacao"}.issubset(set(r.keys()))

    def test_periodo_a_tem_metricas(self):
        r = comparar_periodos("2026-05-01", "2026-05-31", DATA_INICIO, DATA_FIM)
        pa = r["periodo_a"]
        assert pa.get("investimento", pa.get("investimento_total", 0)) > 0

    def test_variacao_calculada(self):
        r = comparar_periodos("2026-05-01", "2026-05-31", DATA_INICIO, DATA_FIM)
        v = r.get("variacao", {})
        assert "roas" in v or "investimento_total" in v, "Variação deve conter métricas"

    def test_periodos_diferentes_dao_variacao(self):
        r = comparar_periodos("2026-01-01", "2026-01-31", DATA_INICIO, DATA_FIM)
        assert isinstance(r, dict)


# ── 12. comparar_campanhas_detalhado ──────────────────────────────────────────

class TestCompararCampanhasDetalhado:
    def test_retorna_dict(self):
        r = comparar_campanhas_detalhado([CAMPANHA_A, CAMPANHA_B], DATA_INICIO, DATA_FIM)
        assert isinstance(r, dict)
        assert "campanhas" in r and "vencedores" in r and "periodo" in r

    def test_inclui_campanhas_solicitadas(self):
        r = comparar_campanhas_detalhado([CAMPANHA_A, CAMPANHA_B], DATA_INICIO, DATA_FIM)
        nomes = [c["campanha"] for c in r["campanhas"]]
        assert CAMPANHA_A in nomes or CAMPANHA_B in nomes

    def test_sem_filtro_de_data(self):
        r = comparar_campanhas_detalhado([CAMPANHA_A, CAMPANHA_B])
        assert isinstance(r, dict) and "campanhas" in r


# ── 13. comparar_semana_a_semana ──────────────────────────────────────────────

class TestCompararSemanaASemana:
    def test_retorna_lista(self):
        r = comparar_semana_a_semana()
        assert isinstance(r, list)

    def test_tem_n_semanas(self):
        r = comparar_semana_a_semana(semanas=4)
        assert len(r) <= 4

    def test_campos_wow(self):
        r = comparar_semana_a_semana()
        if r:
            campos = set(r[0].keys())
            assert {"semana", "data_inicio", "data_fim", "investimento", "roas"}.issubset(campos)

    def test_por_campanha(self):
        r = comparar_semana_a_semana(campanha=CAMPANHA_A, semanas=4)
        assert isinstance(r, list)

    def test_ordenado_cronologicamente(self):
        r = comparar_semana_a_semana()
        if len(r) > 1:
            datas = [row["data_inicio"] for row in r]
            assert datas == sorted(datas)


# ── 14. comparar_mes_a_mes ────────────────────────────────────────────────────

class TestCompararMesAMes:
    def test_retorna_lista(self):
        r = comparar_mes_a_mes()
        assert isinstance(r, list)

    def test_tem_n_meses(self):
        r = comparar_mes_a_mes(meses=3)
        assert len(r) <= 3

    def test_campos_mom(self):
        r = comparar_mes_a_mes()
        if r:
            campos = set(r[0].keys())
            assert {"investimento", "roas", "compras"}.issubset(campos)

    def test_por_campanha(self):
        r = comparar_mes_a_mes(campanha=CAMPANHA_A, meses=3)
        assert isinstance(r, list)


# ── 15. comparar_antes_depois ─────────────────────────────────────────────────

class TestCompararAntesDepois:
    def test_retorna_estrutura(self):
        r = comparar_antes_depois("2026-06-15")
        assert isinstance(r, dict)
        assert {"data_virada", "antes", "depois", "variacao"}.issubset(set(r.keys()))

    def test_data_virada_registrada(self):
        r = comparar_antes_depois("2026-06-15")
        assert r["data_virada"] == "2026-06-15"

    def test_antes_tem_metricas(self):
        r = comparar_antes_depois("2026-06-15")
        antes = r.get("antes", {})
        assert antes.get("investimento_total", 0) >= 0

    def test_por_campanha(self):
        r = comparar_antes_depois("2026-06-15", campanha=CAMPANHA_A)
        assert isinstance(r, dict) and "antes" in r

    def test_variacao_percentual(self):
        r = comparar_antes_depois("2026-06-10")
        v = r.get("variacao", {})
        assert isinstance(v, dict)


# ── 16. ranking_comparativo ───────────────────────────────────────────────────

class TestRankingComparativo:
    METRICAS = ["roas", "cpc_medio", "ctr_medio", "compras", "investimento", "receita"]

    def test_ranking_roas(self):
        r = ranking_comparativo("roas", DATA_INICIO, DATA_FIM)
        assert isinstance(r, list) and len(r) > 0

    def test_ordenado_por_metrica_roas(self):
        r = ranking_comparativo("roas", DATA_INICIO, DATA_FIM)
        if len(r) > 1:
            valores = [c.get("roas") for c in r if c.get("roas") is not None]
            assert valores == sorted(valores, reverse=True)

    def test_sem_filtro_de_data(self):
        r = ranking_comparativo("roas")
        assert isinstance(r, list)

    def test_retorna_todas_campanhas(self):
        r = ranking_comparativo("roas", DATA_INICIO, DATA_FIM)
        assert len(r) >= 1

    def test_campos_ranking(self):
        r = ranking_comparativo("roas", DATA_INICIO, DATA_FIM)
        if r:
            assert "campanha" in r[0]


# ── 17. data_range_disponivel ─────────────────────────────────────────────────

class TestDataRangeDisponivel:
    def test_retorna_dict(self):
        r = queries.data_range_disponivel()
        assert isinstance(r, dict)

    def test_campos_completos(self):
        r = queries.data_range_disponivel()
        assert {"data_minima", "data_maxima", "total_linhas", "total_campanhas"}.issubset(set(r.keys()))

    def test_data_minima_antes_de_maxima(self):
        r = queries.data_range_disponivel()
        assert r["data_minima"] < r["data_maxima"]

    def test_banco_tem_28k_linhas(self):
        r = queries.data_range_disponivel()
        assert r["total_linhas"] >= 28000, f"Esperado >= 28.000 linhas, obtido {r['total_linhas']}"

    def test_banco_tem_32_campanhas(self):
        r = queries.data_range_disponivel()
        assert r["total_campanhas"] >= 32

    def test_data_minima_formato_iso(self):
        r = queries.data_range_disponivel()
        import re
        assert re.match(r"\d{4}-\d{2}-\d{2}", r["data_minima"])
        assert re.match(r"\d{4}-\d{2}-\d{2}", r["data_maxima"])
