"""
tests/test_parsear_reuniao.py

Testa agent.sync_reunioes.parsear_reuniao() isoladamente — zero Drive, zero
Claude, zero banco. Implementa a recomendação da Metodologia aplicada ao
Subagente de Transcrição (Notion): "Qual skill testar primeiro" é
parsear_reuniao(), por ser a Camada 1 (Interpretação) deste subagente —
a mesma posição que agent/nlp.py::interpretar() ocupa no Agente de
Performance principal.

Por que isso importa mais aqui do que lá: quando o parser falha, o
fonte_id já é marcado como processado (ver ja_processado() em
sync_reunioes.py) e a reunião nunca é reprocessada a menos que alguém
rode `--todos` manualmente — a falha é silenciosa e sem segunda chance,
diferente de uma resposta errada no Slack que pelo menos aparece pra
alguém.

Fixtures em tests/fixtures/*.txt simulam o texto já extraído do .docx
(saída de extrair_texto() — parágrafos unidos por \\n), não o .docx
em si, porque parsear_reuniao() já recebe texto puro — não precisa de
python-docx pra isso, só pra ler o arquivo de verdade.

Convenção (igual ao corpus de NLP em agente-performance/tests/fixtures/
nlp_corpus.json): toda fixture que expõe uma falha conhecida e ainda não
corrigida fica marcada como caso "known_gap" via pytest.mark.xfail com
strict=True — documenta o bug permanentemente, falha alto (não
silenciosamente) se alguém corrigir o parser e esquecer de atualizar o
teste pra refletir o comportamento novo.
"""
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(BASE_DIR))

from sync_reunioes import parsear_reuniao  # noqa: E402


def _carregar_fixture(nome: str) -> tuple[str, str]:
    """Lê uma fixture e devolve (texto, nome_arquivo) — a primeira linha
    do arquivo simula o nome do arquivo .docx no Drive (como o Gemini Notes
    nomeia: 'YYYY_MM_DD HH_MM Título - Notas da reunião')."""
    texto = (FIXTURES_DIR / f"{nome}.txt").read_text(encoding="utf-8")
    nome_arquivo = texto.splitlines()[0]
    return texto, nome_arquivo


class TestCasoPadrao:
    """Caso feliz: cabeçalhos de seção exatamente como o parser espera.
    Confirma os 4 campos-chave citados na Metodologia de Teste —
    resumo, decisões, próximas etapas e participantes — saem não-vazios."""

    @pytest.fixture(scope="class")
    @classmethod
    def resultado(cls):
        texto, nome_arquivo = _carregar_fixture("caso_padrao")
        return parsear_reuniao(texto, nome_arquivo)

    def test_data_e_hora_extraidas_do_nome_do_arquivo(self, resultado):
        assert resultado["data"] == "2026-09-28"
        assert resultado["hora"] == "09:00"

    def test_titulo_extraido(self, resultado):
        assert resultado["titulo"] == "2026_09_28 09_00 Reunião Semanal POD"

    def test_resumo_nao_vazio(self, resultado):
        assert resultado["resumo"].strip() != ""
        assert "Black Friday" in resultado["resumo"]

    def test_decisoes_nao_vazio(self, resultado):
        assert resultado["decisoes"].strip() != ""
        assert "ABO" in resultado["decisoes"]

    def test_proximas_etapas_nao_vazio(self, resultado):
        assert resultado["proximas_etapas"].strip() != ""
        assert "criativos" in resultado["proximas_etapas"]

    def test_detalhes_nao_vazio(self, resultado):
        assert resultado["detalhes"].strip() != ""

    @pytest.mark.xfail(
        reason=(
            "known_gap: participantes extraído via linha.replace('convidado', '') "
            "é case-sensitive, mas a checagem de prefixo (linha.lower().startswith) "
            "não é — quando o cabeçalho real vem capitalizado ('Convidado ...', como "
            "o Gemini Notes normalmente exporta), o replace não casa e a palavra "
            "'Convidado' sobra no campo. Fix: usar re.sub(r'(?i)^convidados?\\s*', "
            "'', linha) em vez de .replace(). Achado nesta sessão ao montar a "
            "fixture — não estava documentado no Notion antes disso."
        ),
        strict=True,
    )
    def test_participantes_sem_prefixo_convidado(self, resultado):
        assert resultado["participantes"] == "Cesar, Bianca, Vitor"


class TestCabecalhoAlternativo:
    """known_gap documentado na Metodologia aplicada — Subagente de
    Transcrição (Notion): o parser procura a string literal
    'Próximas etapas'. Quando o Gemini Notes varia a redação do cabeçalho
    (ex: 'Próximos passos'), o campo correspondente sai vazio — e, pior,
    a seção anterior ('Decisões') incorpora por engano o conteúdo da seção
    que devia ter sido cortada ali, porque o delimitador de parada não bateu.

    Isso é 'known_gap' no mesmo sentido do corpus de NLP: documenta um bug
    real e não escondido, não um comportamento aceito como correto. Fica
    xfail(strict=True) até alguém decidir corrigir o parser — nesse caso,
    o fix correto citado na Metodologia Parte A é consertar a skill
    FUNDAMENTAL (o parser, aceitando variações de cabeçalho), nunca
    compensar isso mais acima (ex: no prompt do Claude que analisa a ata)."""

    @pytest.fixture(scope="class")
    @classmethod
    def resultado(cls):
        texto, nome_arquivo = _carregar_fixture("caso_cabecalho_alternativo")
        return parsear_reuniao(texto, nome_arquivo)

    def test_resumo_nao_vazio(self, resultado):
        # Camada anterior ao bug — não afetada, serve de controle.
        assert resultado["resumo"].strip() != ""

    @pytest.mark.xfail(
        reason=(
            "known_gap: cabeçalho 'Próximos passos' (em vez do literal "
            "'Próximas etapas' que o parser procura) faz proximas_etapas sair "
            "vazio. Ver Metodologia aplicada — Subagente de Transcrição (Notion)."
        ),
        strict=True,
    )
    def test_proximas_etapas_nao_vazio(self, resultado):
        assert resultado["proximas_etapas"].strip() != ""

    @pytest.mark.xfail(
        reason=(
            "known_gap (consequência do mesmo bug acima): como o delimitador "
            "'Próximas etapas' não bate, _entre_secoes() pra Decisões não encontra "
            "onde parar e a captura 'vaza' pro texto da seção seguinte inteira — "
            "decisoes fica contaminado com o conteúdo de 'Próximos passos'."
        ),
        strict=True,
    )
    def test_decisoes_nao_contaminado_pela_secao_seguinte(self, resultado):
        assert "Próximos passos" not in resultado["decisoes"]
        assert "proposta atualizada" not in resultado["decisoes"]
