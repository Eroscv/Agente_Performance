"""
Avaliação ponta a ponta do bot (NLP → consulta → redação), com LLM de verdade e banco sintético.

Não roda por padrão: gasta tokens. Na VM, com GEMINI_API_KEY ou ANTHROPIC_API_KEY:
    pytest -m eval tests/test_eval_bot.py
Mede: cliente certo, números iguais aos do banco, nenhum dado de outro cliente.
Mudou prompt, modelo ou pipeline? Rode antes e depois; regressão aqui bloqueia o deploy.
"""
import json
import os
from datetime import date
from pathlib import Path

import pytest

CASOS = json.loads((Path(__file__).resolve().parents[1] / "evals" / "casos.json").read_text())["casos"]
pytestmark = [pytest.mark.eval,
              pytest.mark.skipif(not (os.getenv("GEMINI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")),
                                 reason="sem chave de LLM")]


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_caso(caso, fixture_db, monkeypatch):
    monkeypatch.setattr("agent.nlp.hoje_brt", lambda: date(2026, 6, 8))
    from agent.engine import consultar
    from agent.claude_client import gerar_resposta

    r = consultar(caso["pergunta"])
    texto = r.get("erro") or gerar_resposta(caso["pergunta"], r["dados"], r["intent"], r["meta"],
                                            dados_extra=r.get("dados_extra", {}), usar_kb=False)
    falhas = []
    if r["meta"].get("cliente") not in (caso["cliente"], None):
        falhas.append(f"cliente {r['meta'].get('cliente')} ≠ {caso['cliente']}")
    falhas += [f"faltou {alts}" for alts in caso["deve_conter"] if not any(a in texto for a in alts)]
    falhas += [f"vazou {x!r}" for x in caso["nao_pode_conter"] if x in texto]
    assert not falhas, f"{falhas}\n--- resposta ---\n{texto}"
