"""
tests/test_guards.py

Testa os guards pós-geração (fact-check/compliance) e o recálculo de
confiança que eles alimentam — implementados inspirados no pipeline de
Confidence Score documentado para o agente Flora (CONFIDENCE.md),
adaptados a este agente (métricas de ads, não e-commerce).

Zero chamada a LLM aqui: hallucination_guard.verificar_alucinacao() e
compliance_guard.verificar_compliance() são funções puras sobre texto
+ metadados — testáveis isoladamente, como qualquer skill de Camada 1.
"""
from agent.compliance_guard import verificar_compliance
from agent.confidence import ConfidenceResult, recalcular_pos_guards
from agent.hallucination_guard import verificar_alucinacao


class TestHallucinationGuard:
    def test_sem_problema_quando_resposta_coerente(self):
        meta = {"cliente": "pod_kombucha", "data_inicio": "2026-06-01", "data_fim": "2026-06-07"}
        resposta = "O investimento da campanha ABO foi de R$ 700,00 no período de 2026-06-01 a 2026-06-07."
        assert verificar_alucinacao(resposta, [{"investimento": 700.0}], meta) == []

    def test_cliente_cruzado_quando_resposta_menciona_outro_cliente(self):
        meta = {"cliente": "pod_kombucha", "data_inicio": None, "data_fim": None}
        resposta = "A campanha da Source OF teve o melhor desempenho no período."
        assert "cliente_cruzado" in verificar_alucinacao(resposta, [], meta)

    def test_sem_cliente_cruzado_quando_cliente_pedido_e_none(self):
        """Resposta geral (GeneralAgent, multi-cliente) pode mencionar
        qualquer cliente — não há cliente_key pra cruzar."""
        meta = {"cliente": None}
        resposta = "Comparando POD Kombucha e Source OF, o POD teve ROAS maior."
        assert verificar_alucinacao(resposta, [], meta) == []

    def test_periodo_fora_do_escopo_quando_cita_data_fora_do_pedido(self):
        meta = {"cliente": None, "data_inicio": "2026-06-01", "data_fim": "2026-06-07"}
        resposta = "Os dados de 2026-07-15 mostram uma campanha com ROAS alto."
        assert "periodo_fora_do_escopo" in verificar_alucinacao(resposta, [], meta)

    def test_data_dentro_do_periodo_nao_e_flag(self):
        meta = {"cliente": None, "data_inicio": "2026-06-01", "data_fim": "2026-06-07"}
        resposta = "No dia 2026-06-03 o investimento foi maior que a média."
        assert verificar_alucinacao(resposta, [], meta) == []


class TestComplianceGuard:
    def test_resposta_vazia_e_blank_response(self):
        assert verificar_compliance("") == ["blank_response"]
        assert verificar_compliance("   ") == ["blank_response"]

    def test_resposta_curta_e_short_response(self):
        assert "short_response" in verificar_compliance("Ok.")

    def test_resposta_normal_sem_violacao(self):
        resposta = (
            "A campanha ABO teve investimento de R$ 700,00 e ROAS de 4.0x "
            "no período solicitado, com 14 compras registradas."
        )
        assert verificar_compliance(resposta) == []

    def test_resposta_em_ingles_e_wrong_language(self):
        resposta = "The campaign performance is great and the results are good for the client."
        assert "wrong_language" in verificar_compliance(resposta)

    def test_url_externa_desconhecida_e_external_url(self):
        resposta = "Veja mais detalhes em https://exemplo-suspeito.com/relatorio para referência completa."
        assert "external_url" in verificar_compliance(resposta)

    def test_url_notion_e_permitida(self):
        resposta = "O relatório completo foi publicado em https://notion.so/relatorio-semanal para consulta."
        assert "external_url" not in verificar_compliance(resposta)

    def test_prompt_leak_quando_vaza_marcador_interno(self):
        resposta = "Segundo as [REGRAS DE GROUNDING — OBRIGATÓRIAS] que recebi, o investimento foi R$ 100."
        assert "prompt_leak" in verificar_compliance(resposta)


class TestRecalcularPosGuards:
    def test_sem_guards_acionados_mantem_score(self):
        cr = ConfidenceResult(level="high", score=0.95, evidence=["intent:top_roas"])
        resultado = recalcular_pos_guards(cr, hg_tipos=[], cg_regras=[])
        assert resultado.score == 0.95
        assert resultado.level == "high"

    def test_cliente_cruzado_derruba_pra_medium(self):
        cr = ConfidenceResult(level="high", score=1.0, evidence=[])
        resultado = recalcular_pos_guards(cr, hg_tipos=["cliente_cruzado"], cg_regras=[])
        assert resultado.score == 0.50  # 1.0 - 0.50
        assert resultado.level == "medium"
        assert any("cliente_cruzado" in w for w in resultado.warnings)

    def test_blank_response_zera_a_confianca(self):
        cr = ConfidenceResult(level="high", score=1.0, evidence=[])
        resultado = recalcular_pos_guards(cr, hg_tipos=[], cg_regras=["blank_response"])
        assert resultado.score == 0.50
        assert resultado.level == "medium"

    def test_multiplas_penalidades_combinam_e_clampam_em_zero(self):
        cr = ConfidenceResult(level="high", score=0.90, evidence=[])
        resultado = recalcular_pos_guards(
            cr,
            hg_tipos=["cliente_cruzado", "periodo_fora_do_escopo"],
            cg_regras=["blank_response"],
        )
        # 0.90 - 0.50 - 0.20 - 0.50 = -0.30 → clamp em 0.0
        assert resultado.score == 0.0
        assert resultado.level == "low"
        assert resultado.reason is not None

    def test_evidence_original_e_preservada(self):
        cr = ConfidenceResult(level="high", score=0.9, evidence=["intent:resumo_geral", "periodo:2026-06-01→2026-06-07"])
        resultado = recalcular_pos_guards(cr, hg_tipos=["periodo_fora_do_escopo"], cg_regras=[])
        assert resultado.evidence == cr.evidence
