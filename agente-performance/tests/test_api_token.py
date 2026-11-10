"""API interna fechada por token; /health aberto (offline, sem Slack real)."""
import pytest

pytest.importorskip("slack_bolt")


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-teste")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "teste")
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def test_token(cliente, monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    assert cliente.get("/nao-existe").status_code == 401           # sem API_TOKEN: fechado
    monkeypatch.setenv("API_TOKEN", "s3gredo")
    assert cliente.get("/nao-existe", headers={"x-api-token": "errado"}).status_code == 401
    assert cliente.get("/health").status_code != 401
    assert cliente.get("/nao-existe", headers={"x-api-token": "s3gredo"}).status_code == 404
