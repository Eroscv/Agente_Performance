"""AUD-09: aviso antes de a credencial vencer, falha se inválida, e o valor nunca aparece."""
import time

import pytest

from scripts import tokens


class _R:
    ok = True
    status_code = 200

    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


@pytest.mark.parametrize("data,esperado", [
    ({"is_valid": True, "expires_at": 0, "data_access_expires_at": 0}, "ok"),
    ({"is_valid": True, "expires_at": time.time() + 5 * 86400}, "aviso"),
    ({"is_valid": True, "expires_at": time.time() + 60 * 86400, "data_access_expires_at": time.time() + 3 * 86400}, "aviso"),
    ({"is_valid": True, "expires_at": time.time() + 60 * 86400}, "ok"),
    ({"is_valid": False, "error": {"message": "Session has expired"}}, "falha"),
])
def test_meta(monkeypatch, data, esperado):
    monkeypatch.setenv("META_X", "segredo-123")
    monkeypatch.setattr(tokens.requests, "get", lambda *a, **k: _R({"data": data}))
    st, det = tokens.checar_meta("META_X")
    assert st == esperado and "segredo-123" not in det


def test_vazio_e_excecao_viram_falha_sem_vazar(monkeypatch, capsys):
    monkeypatch.delenv("META_X", raising=False)
    assert tokens.checar_meta("META_X")[0] == "falha"
    monkeypatch.setattr(tokens, "tokens_meta", lambda: [])
    monkeypatch.setattr(tokens, "checar_gads", lambda: (_ for _ in ()).throw(RuntimeError("invalid_grant")))
    monkeypatch.setattr(tokens, "checar_slack", lambda: ("ok", "token aceito"))
    assert tokens.verificar() == [("Google Ads", "falha", "RuntimeError: invalid_grant"), ("Slack", "ok", "token aceito")]


def test_lista_os_tokens_do_clientes_json():
    nomes = tokens.tokens_meta()
    assert "META_ACCESS_TOKEN" in nomes and all(n.endswith("TOKEN") for n in nomes)
