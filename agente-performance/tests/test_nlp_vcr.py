"""
tests/test_nlp_vcr.py

Protótipo do pré-requisito da seção 1 da "Metodologia de Teste de Skills"
(Notion): "Mock de LLM... via respostas gravadas (cassette) — sem isso,
todo teste que passa por um subagente custa dinheiro e é não-determinístico."

Usa vcrpy para gravar/reproduzir a chamada HTTP que agent.nlp.interpretar()
faz ao Gemini via google-genai (biblioteca httpx por baixo — confirmado por
introspecção em scripts/_gerar_cassette_demo.py). Com o cassette já gravado
em tests/cassettes/, este teste roda OFFLINE e DETERMINÍSTICO, sem
GEMINI_API_KEY real e sem custo de API — é esse o ganho sobre o modo
fallback_regex que scripts/avaliar_nlp.py usa hoje.

IMPORTANTE — o que este protótipo prova e o que NÃO prova:
  PROVA: o mecanismo de cassette funciona fim-a-fim com o código real do
  projeto (agent.nlp.interpretar → agent.claude_client._call_llm →
  google.genai → httpx), e dá pra rodar no CI sem nenhuma chave de API.
  NÃO PROVA precisão do NLP real — a resposta dentro do cassette foi
  ESCRITA À MÃO (copiada do few-shot #16 já existente em
  agent.nlp.NLP_SYSTEM_PROMPT), não veio de uma chamada real ao Gemini.
  Isso é deixado explícito dentro do próprio cassette (campo
  '_cassette_sintetica') e aqui.

Para gravar um cassette com uma resposta REAL do Gemini (recomendado assim
que isso rodar numa máquina com GEMINI_API_KEY de verdade): apagar o arquivo
em tests/cassettes/ e trocar vcr_cassette.record_mode para "once" — a
vcrpy grava a interação real na primeira execução e passa a reproduzi-la
nas seguintes, sem precisar da chave depois disso.
"""
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import vcr as vcrpy

BASE_DIR = Path(__file__).parent.parent
CASSETTE_DIR = BASE_DIR / "tests" / "cassettes"

my_vcr = vcrpy.VCR(
    cassette_library_dir=str(CASSETTE_DIR),
    record_mode="none",  # nunca bate na rede neste repo — só reproduz o que já foi gravado
    match_on=["method", "scheme", "host", "port", "path", "body"],
    decode_compressed_response=True,
)


@pytest.fixture
def gemini_api_key_fake(monkeypatch):
    """
    _call_gemini só tenta a chamada se a env var existir e não for vazia —
    o valor em si não importa pro replay porque não casamos por header
    (a chave real iria no header x-goog-api-key).
    """
    monkeypatch.setenv("GEMINI_API_KEY", "cassette-demo-key")
    monkeypatch.delenv("GEMINI_API_KEY_BACKUP", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def hoje_congelado(monkeypatch):
    """Congela 'hoje' para a mesma data usada ao gravar o cassette —
    o system prompt embute a data, então tem que bater com o corpo gravado."""
    monkeypatch.setattr("agent.nlp.hoje_brt", lambda: date(2026, 10, 19))


@pytest.fixture(autouse=True)
def limpar_cache_nlp():
    """
    Pegadinha real encontrada construindo este protótipo: agent.nlp.interpretar()
    tem um cache próprio (RAM + SQLite em data/campanhas.db, TTL 2h) que roda
    ANTES de qualquer chamada ao LLM. Sem limpar, um resultado cacheado de uma
    execução anterior faz o teste "passar" sem NUNCA tocar no cassette — ele
    testaria o cache, não o parser. Foi exatamente o que aconteceu uma vez
    construindo este arquivo (achei o cassette vazio em disco depois de ver
    o pytest passar). Mesma lógica de tests/test_engine_validacao.py::limpar_cache().
    """
    import sqlite3
    db_path = BASE_DIR / "data" / "campanhas.db"
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("DELETE FROM nlp_cache")
            conn.commit()
        except Exception:
            pass
        conn.close()
    yield


class TestNLPComCassette:

    @my_vcr.use_cassette("nlp_top_roas_pod_google.yaml")
    def test_interpretar_top_roas_pod_google_via_cassette(self, gemini_api_key_fake, hoje_congelado):
        from agent.nlp import interpretar

        resultado = interpretar(
            "top roas search e pmax do pod",
            campanhas_disponiveis=[],
            contexto="",
        )

        # Estas são as asserções que o modo fallback_regex (usado por
        # scripts/avaliar_nlp.py sem API key) NÃO consegue satisfazer hoje —
        # é exatamente o fosso que o cassette fecha: aqui testamos o parser
        # Gemini de verdade (via resposta gravada), não o regex degradado.
        assert resultado["intents"] == ["top_roas"]
        assert resultado["cliente_key"] == "pod_kombucha"
        assert resultado["plataforma"] == "google_ads"
        assert resultado["campanha"] is None

    @my_vcr.use_cassette("nlp_top_roas_pod_google.yaml")
    def test_pergunta_sem_cassette_cai_no_fallback_sem_tocar_rede(self, gemini_api_key_fake, hoje_congelado):
        """
        Prova negativa: com record_mode='none' e cassette presente, uma
        pergunta que NÃO tem interação gravada correspondente nunca sai
        para a rede de verdade — vcrpy recusa (CannotOverwriteExistingCassetteException,
        visível nos logs como "No match for the request... found"), e
        agent.claude_client._call_llm captura esse erro (Gemini + Anthropic
        indisponíveis) e cai no fallback regex de agent.nlp._fallback_regex.
        Isso é visível no resultado: raw['fallback'] is True. Checar esse
        flag é a forma de provar isolamento de rede sem depender de inspecionar
        os logs capturados pelo pytest.
        """
        from agent.nlp import interpretar

        resultado = interpretar(
            "pergunta sem cassette gravado, só pra provar isolamento de rede",
            campanhas_disponiveis=[], contexto="",
        )
        assert resultado["raw"].get("fallback") is True, (
            "Esperado cair no fallback regex (nenhuma chamada de rede real deveria "
            f"ter sido possível) — resultado obtido: {resultado}"
        )
