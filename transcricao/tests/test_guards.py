"""
tests/test_guards.py

Testa os guards pós-geração do AnaliseAgent (Camada 2) — fact-check do
JSON devolvido por analisar_com_claude() contra o texto real da
reunião, e checagens de esquema/qualidade. Zero chamada ao Claude:
hallucination_guard.verificar_alucinacao() e
compliance_guard.verificar_compliance() são funções puras sobre o dict
já parseado + a reunião (dict ou sqlite3.Row).

Mesmo princípio do par homônimo em agente-performance/agent/
hallucination_guard.py + compliance_guard.py, adaptado ao formato
deste subagente (JSON estruturado, não texto livre pro usuário).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compliance_guard import verificar_compliance  # noqa: E402
from hallucination_guard import verificar_alucinacao  # noqa: E402

REUNIAO_BASE = {
    "titulo": "Reunião Semanal POD",
    "participantes": "Cesar, Bianca",
    "resumo": "A equipe revisou os indicadores de performance da semana.",
    "decisoes": "Aumentar o orçamento da campanha ABO em 20%.",
    "proximas_etapas": "Cesar vai revisar os criativos até quinta-feira.",
    "detalhes": "Discussão sobre CPM crescente nas últimas duas semanas.",
}


class TestHallucinationGuard:
    def test_sem_problema_quando_trecho_existe_no_texto(self):
        resultado = {
            "alertas": [{"tipo": "urgente", "trecho": "CPM crescente nas últimas duas semanas", "descricao": "CPM subindo"}],
        }
        assert verificar_alucinacao(resultado, REUNIAO_BASE) == []

    def test_trecho_nao_encontrado_quando_alerta_inventa_trecho(self):
        resultado = {
            "alertas": [{"tipo": "urgente", "trecho": "cliente ameaçou cancelar o contrato", "descricao": "risco de churn"}],
        }
        assert "trecho_nao_encontrado" in verificar_alucinacao(resultado, REUNIAO_BASE)

    def test_tipo_alerta_invalido_quando_fora_do_vocabulario(self):
        resultado = {
            "alertas": [{"tipo": "churn_risco", "trecho": "CPM crescente", "descricao": "x"}],
        }
        assert "tipo_alerta_invalido" in verificar_alucinacao(resultado, REUNIAO_BASE)

    def test_sem_alertas_nao_gera_problema(self):
        assert verificar_alucinacao({"alertas": []}, REUNIAO_BASE) == []

    def test_funciona_com_sqlite_row_like_mapping(self):
        """reuniao vem de sqlite3.Row em produção — garante que o guard
        não depende de ser um dict puro."""
        class RowFake(dict):
            pass

        reuniao_row = RowFake(REUNIAO_BASE)
        resultado = {"alertas": [{"tipo": "urgente", "trecho": "orçamento da campanha ABO", "descricao": "x"}]}
        assert verificar_alucinacao(resultado, reuniao_row) == []


class TestComplianceGuard:
    def test_resultado_valido_sem_violacao(self):
        resultado = {
            "sentimento": "neutro",
            "alertas": [],
            "clientes_mencionados": [],
            "resumo_analise": "Reunião produtiva, equipe alinhada sobre os próximos passos da campanha.",
        }
        assert verificar_compliance(resultado) == []

    def test_schema_invalido_quando_falta_chave_obrigatoria(self):
        resultado = {"sentimento": "neutro", "alertas": []}  # falta clientes_mencionados e resumo_analise
        assert "schema_invalido" in verificar_compliance(resultado)

    def test_schema_invalido_quando_nao_e_dict(self):
        assert "schema_invalido" in verificar_compliance("não é um dict")

    def test_sentimento_invalido_fora_do_vocabulario(self):
        resultado = {"sentimento": "ansioso", "alertas": [], "clientes_mencionados": [], "resumo_analise": "Texto normal aqui."}
        assert "sentimento_invalido" in verificar_compliance(resultado)

    def test_blank_response_quando_resumo_vazio(self):
        resultado = {"sentimento": "neutro", "alertas": [], "clientes_mencionados": [], "resumo_analise": ""}
        assert "blank_response" in verificar_compliance(resultado)

    def test_wrong_language_quando_resumo_em_ingles(self):
        resultado = {
            "sentimento": "neutro", "alertas": [], "clientes_mencionados": [],
            "resumo_analise": "The meeting was good and the team is aligned with the client goals.",
        }
        assert "wrong_language" in verificar_compliance(resultado)

    def test_prompt_leak_quando_resumo_ecoa_prompt_sistema(self):
        resultado = {
            "sentimento": "neutro", "alertas": [], "clientes_mencionados": [],
            "resumo_analise": "Lembrete: responda apenas com json válido conforme instruído, segue o resumo.",
        }
        assert "prompt_leak" in verificar_compliance(resultado)
