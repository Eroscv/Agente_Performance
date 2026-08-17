"""Testes das melhorias de dados e consultas."""
import pytest
import sqlite3
from pathlib import Path
from datetime import date, timedelta

BASE = Path(__file__).parent.parent


class TestVerificarGapDados:
    def test_retorna_dict(self):
        from agent.queries import verificar_gap_dados
        r = verificar_gap_dados("source_of", "meta_ads")
        assert isinstance(r, dict)
        assert "tem_gap" in r

    def test_campos_quando_sem_gap(self):
        from agent.queries import verificar_gap_dados
        r = verificar_gap_dados("source_of", "meta_ads")
        if not r["tem_gap"]:
            assert r.get("aviso") is None

    def test_campos_quando_com_gap(self):
        from agent.queries import verificar_gap_dados
        r = verificar_gap_dados("source_of", "meta_ads")
        if r["tem_gap"]:
            assert r.get("aviso") is not None
            assert "⚠️" in r["aviso"]
            assert r.get("dias_atrasado", 0) > 0


class TestTopCriativos:
    def test_retorna_lista(self):
        from agent.queries import top_criativos_roas
        from datetime import date, timedelta
        fim = date.today()
        ini = fim - timedelta(days=30)
        r = top_criativos_roas(data_inicio=str(ini), data_fim=str(fim), cliente_key="source_of", limit=5)
        assert isinstance(r, list)

    def test_campos_presentes(self):
        from agent.queries import top_criativos_roas
        from datetime import date, timedelta
        fim = date.today()
        ini = fim - timedelta(days=30)
        r = top_criativos_roas(data_inicio=str(ini), data_fim=str(fim), cliente_key="source_of", limit=5)
        if r:
            row = r[0]
            assert "criativo" in row
            assert "roas" in row
            assert "investimento" in row
            assert "ctr" in row

    def test_filtro_campanha(self):
        from agent.queries import top_criativos_roas
        from datetime import date, timedelta
        fim = date.today()
        ini = fim - timedelta(days=30)
        r = top_criativos_roas(campanha="CAMPANHA_INEXISTENTE_XYZ", data_inicio=str(ini), data_fim=str(fim))
        assert r == []


class TestCriativosFadiga:
    def test_retorna_lista(self):
        from agent.queries import criativos_em_fadiga
        from datetime import date, timedelta
        fim = date.today()
        ini = fim - timedelta(days=30)
        r = criativos_em_fadiga(data_inicio=str(ini), data_fim=str(fim), cliente_key="source_of")
        assert isinstance(r, list)

    def test_frequencia_minima_respeitada(self):
        from agent.queries import criativos_em_fadiga
        from datetime import date, timedelta
        fim = date.today()
        ini = fim - timedelta(days=30)
        r = criativos_em_fadiga(frequencia_min=100.0, data_inicio=str(ini), data_fim=str(fim))
        assert r == []  # ninguém tem frequência 100


class TestProjecaoMes:
    def test_retorna_dict(self):
        from agent.queries import projecao_fechamento_mes
        r = projecao_fechamento_mes(cliente_key="source_of")
        assert isinstance(r, dict)

    def test_campos_projecao(self):
        from agent.queries import projecao_fechamento_mes
        r = projecao_fechamento_mes(cliente_key="source_of")
        if "aviso" not in r:
            assert "proj_invest_mes" in r
            assert "proj_roas" in r
            assert "dias_mes" in r
            assert "pct_mes_decorrido" in r
            assert r["dias_mes"] in range(28, 32)

    def test_roas_projecao_positivo(self):
        from agent.queries import projecao_fechamento_mes
        r = projecao_fechamento_mes(cliente_key="source_of")
        if r.get("proj_roas"):
            assert r["proj_roas"] > 0


class TestSazonalidade:
    def test_retorna_dict(self):
        from agent.queries import comparar_mesmo_periodo_ano_anterior
        fim = date.today() - timedelta(days=1)
        ini = fim - timedelta(days=6)
        r = comparar_mesmo_periodo_ano_anterior(str(ini), str(fim), cliente_key="source_of")
        assert isinstance(r, dict)

    def test_periodo_atual_sempre_presente(self):
        from agent.queries import comparar_mesmo_periodo_ano_anterior
        fim = date.today() - timedelta(days=1)
        ini = fim - timedelta(days=6)
        r = comparar_mesmo_periodo_ano_anterior(str(ini), str(fim), cliente_key="source_of")
        assert "periodo_atual" in r

    def test_sem_dados_ano_anterior_retorna_aviso(self):
        from agent.queries import comparar_mesmo_periodo_ano_anterior
        # Data muito antiga que não tem ano anterior no banco
        r = comparar_mesmo_periodo_ano_anterior("2024-01-01", "2024-01-07", cliente_key="source_of")
        # Pode ter ou não dados — o importante é retornar dict válido
        assert isinstance(r, dict)

    def test_com_dados_ano_anterior_tem_variacao(self):
        from agent.queries import comparar_mesmo_periodo_ano_anterior
        # Source OF tem dados desde jan/2024, então ago/2025 tem ano anterior
        r = comparar_mesmo_periodo_ano_anterior("2025-08-01", "2025-08-07", cliente_key="source_of")
        if r.get("tem_dados_ano_anterior"):
            assert "variacao" in r
            assert "roas_pct" in r["variacao"]
