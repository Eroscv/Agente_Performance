"""
tests/test_orquestrador.py

Testa OrquestradorTranscricao com subagentes fake injetados — nenhuma
chamada real a Drive/Claude/Notion/Slack/BigQuery. Confirma o
comportamento central: as 5 etapas rodam na ordem certa, uma falha numa
etapa não impede as seguintes, e --pular-comunicacao pula só a Camada 4.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from orquestrador import OrquestradorTranscricao  # noqa: E402


class _AgenteFake:
    """Fake de subagente: registra que foi chamado (com que kwargs) numa
    lista compartilhada, e devolve um resumo fixo ou levanta uma exceção."""

    def __init__(self, nome: str, chamadas: list, falha: Exception | None = None):
        self.nome = nome
        self.chamadas = chamadas
        self.falha = falha

    def executar(self, **kwargs) -> dict:
        self.chamadas.append((self.nome, kwargs))
        if self.falha:
            raise self.falha
        return {"nome": self.nome, "ok": True}


def _montar_orquestrador(chamadas: list, falha_em: str | None = None) -> OrquestradorTranscricao:
    def fake(nome):
        return _AgenteFake(nome, chamadas, falha=RuntimeError(f"falha simulada em {nome}") if nome == falha_em else None)

    return OrquestradorTranscricao(
        ingestao=fake("ingestao"),
        analise=fake("analise"),
        ticket=fake("ticket"),
        metricas_agent=fake("metricas"),
        comunicacao=fake("comunicacao"),
    )


def test_roda_as_5_etapas_na_ordem_certa():
    chamadas = []
    orq = _montar_orquestrador(chamadas)
    resultado = orq.executar()

    ordem_esperada = ["ingestao", "analise", "ticket", "metricas", "comunicacao"]
    assert [nome for nome, _ in chamadas] == ordem_esperada
    assert set(resultado.keys()) == set(ordem_esperada)
    assert all(r["status"] == "ok" for r in resultado.values())


def test_falha_em_uma_etapa_nao_impede_as_seguintes():
    chamadas = []
    orq = _montar_orquestrador(chamadas, falha_em="analise")
    resultado = orq.executar()

    # Todas as 5 etapas foram chamadas, mesmo com a análise falhando no meio.
    assert [nome for nome, _ in chamadas] == ["ingestao", "analise", "ticket", "metricas", "comunicacao"]

    assert resultado["ingestao"]["status"] == "ok"
    assert resultado["analise"]["status"] == "erro"
    assert "falha simulada em analise" in resultado["analise"]["erro"]
    assert resultado["ticket"]["status"] == "ok"
    assert resultado["metricas"]["status"] == "ok"
    assert resultado["comunicacao"]["status"] == "ok"


def test_pular_comunicacao_pula_so_a_camada_4():
    chamadas = []
    orq = _montar_orquestrador(chamadas)
    resultado = orq.executar(pular_comunicacao=True)

    assert [nome for nome, _ in chamadas] == ["ingestao", "analise", "ticket", "metricas"]
    assert "comunicacao" not in resultado
    assert set(resultado.keys()) == {"ingestao", "analise", "ticket", "metricas"}


def test_propaga_todos_e_dry_run_pras_etapas_certas():
    chamadas = []
    orq = _montar_orquestrador(chamadas)
    orq.executar(todos=True, dry_run=True)

    por_nome = dict(chamadas)
    assert por_nome["ingestao"] == {"todos": True}
    assert por_nome["analise"] == {"todos": True, "dry_run": True}
    assert por_nome["ticket"] == {"dry_run": True}
    assert por_nome["metricas"] == {"janela_dias": 90, "bq": False}  # dry_run=True -> bq=False
    assert por_nome["comunicacao"] == {"todos": True, "dry_run": True}
