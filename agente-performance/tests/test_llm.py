"""utils.llm.gerar: Gemini primeiro, Claude só como reserva quando há chave."""
import pytest

import utils.llm as llm


def _falha(*a, **k):
    raise RuntimeError("fora")


def test_gemini_responde(monkeypatch):
    monkeypatch.setattr(llm, "_gemini", lambda *a: " oi ")
    monkeypatch.setattr(llm, "_claude", _falha)
    assert llm.gerar("s", "u") == "oi"


def test_reserva_claude_so_com_chave(monkeypatch):
    monkeypatch.setattr(llm, "_gemini", _falha)
    monkeypatch.setattr(llm, "_claude", lambda *a: "claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Gemini: fora"):
        llm.gerar("s", "u")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert llm.gerar("s", "u") == "claude"
