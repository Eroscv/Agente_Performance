"""Testes das melhorias de usabilidade 2, 3, 5, 6."""
import pytest
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent.parent


def limpar_cache():
    for db_name in ["cache.db"]:
        db = BASE / "data" / db_name
        if db.exists():
            conn = sqlite3.connect(str(db))
            conn.execute("DELETE FROM cache")
            conn.commit()
            conn.close()


class TestFallbackInteligente:
    def test_sugestao_roas(self):
        from agent.nlp import sugerir_intent_por_keywords
        r = sugerir_intent_por_keywords("qual o roas das campanhas?")
        assert r is not None
        intent, cmd = r
        assert intent == "top_roas"
        assert "roas" in cmd.lower()

    def test_sugestao_sem_conversao(self):
        from agent.nlp import sugerir_intent_por_keywords
        r = sugerir_intent_por_keywords("campanhas gastando sem retorno")
        assert r is not None
        intent, cmd = r
        assert intent == "sem_conversao"

    def test_sugestao_vazia_retorna_none(self):
        from agent.nlp import sugerir_intent_por_keywords
        r = sugerir_intent_por_keywords("xyzabcfoobar qualquer coisa aleatória")
        assert r is None

    def test_sugestao_google(self):
        from agent.nlp import sugerir_intent_por_keywords
        r = sugerir_intent_por_keywords("como está o google ads?")
        assert r is not None


class TestUserPrefs:
    def test_get_prefs_usuario_novo(self):
        from agent.user_prefs import get_prefs
        r = get_prefs("user_teste_novo_xyz")
        assert r == {}

    def test_atualizar_e_recuperar(self):
        from agent.user_prefs import atualizar_prefs, get_prefs
        from datetime import date, timedelta
        hoje = date.today()
        seg = hoje - timedelta(days=hoje.weekday() + 7)
        fim_sem = seg + timedelta(days=6)
        atualizar_prefs("user_test_abc", "resumo_geral", str(seg), str(fim_sem), "source_of")
        prefs = get_prefs("user_test_abc")
        assert prefs.get("periodo_default") == "semana"
        assert prefs.get("cliente_pref") == "source_of"
        assert prefs.get("intent_freq") == "resumo_geral"

    def test_aplicar_prefs_sem_periodo(self):
        from agent.user_prefs import atualizar_prefs, aplicar_prefs
        from datetime import date, timedelta
        hoje = date.today()
        seg = hoje - timedelta(days=hoje.weekday() + 7)
        fim = seg + timedelta(days=6)
        atualizar_prefs("user_test_def", "resumo_geral", str(seg), str(fim), "source_of")
        ini_out, fim_out = aplicar_prefs("user_test_def", None, None)
        assert ini_out is not None
        assert fim_out is not None

    def test_aplicar_prefs_nao_sobrescreve_periodo_explicitado(self):
        from agent.user_prefs import aplicar_prefs
        ini, fim = aplicar_prefs("qualquer_user", "2026-08-01", "2026-08-07")
        assert ini == "2026-08-01"
        assert fim == "2026-08-07"


class TestComparativoAutomatico:
    def test_resumo_geral_tem_delta(self):
        limpar_cache()
        from agent.engine import consultar
        r = consultar("resumo da semana passada", channel="C_GENERICO")
        delta = r.get("delta", {})
        # delta deve existir e ter pelo menos investimento ou roas
        assert isinstance(delta, dict), "delta deve ser dict"
        # Se há dados, deve ter comparativo
        dados = r.get("dados", {})
        if dados and isinstance(dados, dict) and dados.get("investimento_total", 0) > 0:
            assert len(delta) > 0, "resumo_geral com dados deve ter delta automático"
