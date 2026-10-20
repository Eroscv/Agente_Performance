#!/usr/bin/env python3
"""
scripts/_gerar_cassette_demo.py — uso único, não faz parte da suíte de testes.

Gera tests/cassettes/nlp_top_roas_pod_google.yaml deixando o VCR.py gravar a
interação ele mesmo (record_mode="all"), com a resposta de rede substituída
por uma resposta fake no nível de TRANSPORTE do httpx.

Lição aprendida tentando duas formas que pareciam razoáveis e falharam:
  1ª tentativa: escrever o YAML à mão com yaml.safe_dump. Rodava, mas o
     comparador de 'body' do vcrpy no replay não batia — o serializer
     interno do vcrpy não é um round-trip ingênuo de yaml.safe_load/dump.
  2ª tentativa: monkeypatch de httpx.Client.send() pra devolver a resposta
     fake. Rodava, até... não gravar nada (cassette.dirty nunca virava True).
     Motivo (visto lendo vcr/stubs/httpx_stubs.py): o VCR.py intercepta no
     nível do TRANSPORTE (`HTTPTransport.handle_request`), não em
     `Client.send()` — esse último chama `self._transport.handle_request()`
     por baixo. Ao substituir `Client.send` inteiro, a chamada nunca passa
     pelo transporte, então o hook do VCR nunca roda e `cassette.append()`
     nunca é chamado.
Solução: substituir `httpx.HTTPTransport.handle_request` (não `Client.send`)
ANTES de `import vcr` — não só antes de `use_cassette(...)`. vcr.patch
guarda a referência "real" de handle_request já no import do pacote `vcr`
(não de forma preguiçosa dentro de use_cassette), então `import vcr` tem que
vir DEPOIS do monkeypatch, senão o VCR.py embrulha o handle_request
ORIGINAL de verdade (pré-patch) e meu fake nunca é chamado — foi exatamente
isso que aconteceu na 3ª tentativa: com `import vcr as vcrpy` no topo do
arquivo (antes de main() rodar o patch), a requisição saía para a rede real
e tomava 403 do proxy do sandbox, sem o VCR.py nunca ver nada pra gravar.

A resposta em si continua sendo ESCRITA À MÃO (não é uma chamada real ao
Gemini) — copiada do few-shot #16 já presente em agent.nlp.NLP_SYSTEM_PROMPT
para esta mesma pergunta. Isso fica marcado dentro do próprio corpo
('_cassette_sintetica': true).

Rodar de novo sempre que agent/nlp.py::NLP_SYSTEM_PROMPT mudar:
  GEMINI_API_KEY=cassette-demo-key python3 scripts/_gerar_cassette_demo.py
"""
import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx  # precisa vir e ser corrigido ANTES de `import vcr` — ver docstring

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

CASSETTE_DIR = BASE_DIR / "tests" / "cassettes"
CASSETTE_NAME = "nlp_top_roas_pod_google.yaml"
PERGUNTA = "top roas search e pmax do pod"

RESPOSTA_ESPERADA = {
    "intents": ["top_roas"], "campanha": None, "data_inicio": None, "data_fim": None,
    "periodo_a_inicio": None, "periodo_a_fim": None, "periodo_b_inicio": None, "periodo_b_fim": None,
    "data_virada": None, "metrica": None, "limit": 10,
    "cliente_key": "pod_kombucha", "plataforma": "google_ads",
}


def fake_handle_request(self, request):
    """Substitui o transporte HTTP real — isto roda ABAIXO do hook do VCR,
    então o VCR vê e grava a troca normalmente."""
    resposta_texto = json.dumps(RESPOSTA_ESPERADA, ensure_ascii=False)
    body = {
        "candidates": [{"content": {"parts": [{"text": resposta_texto}], "role": "model"}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        "_cassette_sintetica": True,
        "_nota": "Resposta escrita à mão para teste — não é uma chamada real ao Gemini. Ver scripts/_gerar_cassette_demo.py.",
    }
    return httpx.Response(
        status_code=200,
        headers={"content-type": "application/json"},
        content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        request=request,
    )


# O patch tem que acontecer ANTES de `import vcr` (ver docstring do módulo) —
# por isso fica aqui no nível de módulo, não dentro de main().
httpx.HTTPTransport.handle_request = fake_handle_request
import vcr as vcrpy  # noqa: E402


def main():
    CASSETTE_DIR.mkdir(parents=True, exist_ok=True)
    cassette_path = CASSETTE_DIR / CASSETTE_NAME
    if cassette_path.exists():
        cassette_path.chmod(0o644)
        cassette_path.unlink()

    my_vcr = vcrpy.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="all",
        match_on=["method", "scheme", "host", "port", "path", "body"],
    )

    with my_vcr.use_cassette(CASSETTE_NAME) as cassette:
        with patch("agent.nlp.hoje_brt", lambda: date(2026, 10, 19)):
            from agent.nlp import interpretar
            resultado = interpretar(PERGUNTA, campanhas_disponiveis=[], contexto="")
        print("cassette.dirty antes de sair do bloco:", cassette.dirty)

    print("Resultado parseado durante a gravação:", resultado)
    existe = cassette_path.exists()
    print(f"Cassette em {cassette_path} — existe? {existe}")
    if not existe:
        print("FALHA: nada foi persistido. Não torne o arquivo read-only.")
        sys.exit(1)

    # Protege contra o efeito colateral descoberto depois: com record_mode="none"
    # (usado nos testes reais), uma 2ª requisição sem match no MESMO cassette
    # pode truncar o arquivo ao tentar abri-lo pra escrita. Read-only é defesa
    # extra; a defesa principal é usar cassettes SEPARADOS por cenário nos testes.
    cassette_path.chmod(0o444)
    print("Cassette marcado como somente-leitura.")


if __name__ == "__main__":
    main()
