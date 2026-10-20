"""
tests/test_agents_estrutura.py

Teste estrutural do pacote agents/ — confirma que cada subagente do
pipeline (um por Camada da Metodologia aplicada — Subagente de
Transcrição, no Notion) está corretamente ligado ao contrato comum
(BaseSubagente), sem precisar mockar Drive/Claude/Notion/Slack/BigQuery.

Não testa comportamento de negócio (isso é escopo de cada camada
individualmente — ParserAgent já tem tests/test_parsear_reuniao.py; as
demais ainda precisam de mocks de LLM/API externa antes de ganhar teste
de comportamento, como qualquer camada 2+ na Metodologia de Teste de
Skills do Agente de Performance).
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

import pytest

from agents import (
    PIPELINE,
    AnaliseAgent,
    BaseSubagente,
    ComunicacaoAgent,
    IngestaoAgent,
    MetricasAgent,
    ParserAgent,
    TicketAgent,
)


@pytest.mark.parametrize("classe", PIPELINE)
def test_subagente_herda_de_base(classe):
    assert issubclass(classe, BaseSubagente)


@pytest.mark.parametrize("classe", PIPELINE)
def test_subagente_declara_camada(classe):
    assert classe.CAMADA, f"{classe.__name__} não declarou CAMADA"


@pytest.mark.parametrize("classe", PIPELINE)
def test_subagente_implementa_executar(classe):
    # BaseSubagente.executar é @abstractmethod — se alguma subclasse não
    # sobrescrever, instanciar levanta TypeError. Isso detecta herança
    # quebrada sem precisar chamar nenhuma API externa.
    instancia = classe()
    assert callable(instancia.executar)


def test_pipeline_cobre_as_5_camadas_documentadas():
    camadas = {classe.CAMADA for classe in PIPELINE}
    assert len(camadas) == len(PIPELINE), "duas classes não deveriam compartilhar a mesma CAMADA"
    # 6 subagentes, 5 Camadas (0,1,2,3,4) — Camada 3 tem 2 subagentes
    # (TicketAgent e MetricasAgent), as outras têm 1 cada.
    prefixos = sorted(c.split(" ")[0] for c in camadas)
    assert prefixos == ["0", "1", "2", "3", "3", "4"]


def test_ingestao_compoe_parser_por_padrao():
    """IngestaoAgent delega a Camada 1 a um ParserAgent — por composição,
    não herança, e por padrão (sem precisar injetar nada)."""
    agente = IngestaoAgent()
    assert isinstance(agente._parser, ParserAgent)


def test_ingestao_aceita_parser_injetado():
    """Permite injetar um ParserAgent (ex: um fake/mock em teste de
    comportamento futuro) sem mudar a classe."""
    fake_parser = ParserAgent()
    agente = IngestaoAgent(parser=fake_parser)
    assert agente._parser is fake_parser


@pytest.mark.parametrize(
    "classe,metodo",
    [
        (ParserAgent, "parsear"),
        (AnaliseAgent, "analisar"),
        (TicketAgent, "abrir_tickets_pendentes"),
        (MetricasAgent, "ic_wilson"),
        (ComunicacaoAgent, "extrair_tarefas"),
    ],
)
def test_metodos_de_dominio_existem(classe, metodo):
    """Confirma que cada subagente expõe pelo menos um método de domínio
    mais granular que executar() — é o que torna composição/teste
    isolado possível (ex: ParserAgent.parsear() já é testado direto em
    test_parsear_reuniao.py, sem passar por executar())."""
    assert hasattr(classe, metodo)
