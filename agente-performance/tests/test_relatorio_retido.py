"""AUD-08 e AUD-18: relatório com dado atrasado ou API fora é retido, e o retido sai quando o dado se corrige."""
import sys
from datetime import date

import pytest


@pytest.fixture
def r(monkeypatch, tmp_path):
    from scripts import relatorio_diario as r
    from scripts import qualidade_dados as q
    import utils.idempotencia as idem
    monkeypatch.setattr(idem, "DB_PATH", tmp_path / "idem.db")
    alertas = []
    monkeypatch.setattr(q, "_alertar", lambda texto, chave: alertas.append(chave))
    r._alertas_teste = alertas
    return r


def _rodar_main(r, monkeypatch, hoje, prontos, enviados):
    """prontos: dias cujo relatório consegue sair; os outros ficam retidos."""
    import agent.tz
    monkeypatch.setattr(agent.tz, "hoje_brt", lambda: hoje)
    monkeypatch.setattr(r.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["relatorio_diario.py", "--cliente", "source_of"])
    monkeypatch.setattr(r, "enviar_relatorio_cliente",
                        lambda data, c, p: (enviados.append((data, p)), data in prontos)[1])
    r.main()


def test_retido_hoje_sai_amanha_e_nao_duplica(r, monkeypatch):
    enviados = []
    _rodar_main(r, monkeypatch, date(2026, 11, 18), prontos=set(), enviados=enviados)   # 17/11 retido
    assert {d for d, _ in enviados} == {"2026-11-15", "2026-11-16", "2026-11-17"}
    enviados.clear()
    _rodar_main(r, monkeypatch, date(2026, 11, 19), prontos={"2026-11-17", "2026-11-18"}, enviados=enviados)
    assert {d for d, _ in enviados} >= {"2026-11-17", "2026-11-18"}                     # o retido saiu
    enviados.clear()
    _rodar_main(r, monkeypatch, date(2026, 11, 20), prontos={"2026-11-17", "2026-11-18", "2026-11-19"}, enviados=enviados)
    assert not [d for d, _ in enviados if d in ("2026-11-17", "2026-11-18")]               # já enviados: não repete


def _segmento(r, monkeypatch, resumo, ultimo, conferencia):
    monkeypatch.setattr(r, "_resumo_dia", lambda *a: resumo)
    monkeypatch.setattr(r, "query_one", lambda sql, args: {"u": ultimo})
    monkeypatch.setattr(r, "conferir_fonte", lambda *a: conferencia)
    postou = []
    monkeypatch.setattr(r, "_post", lambda payload: (postou.append(payload), {"ok": True, "ts": "1"})[1])
    monkeypatch.setattr(r, "gerar_relatorio_diario", lambda **k: {})
    for f in ("_campanhas_dia", "_alertas", "_dia_anterior", "_shopify_dia"):
        monkeypatch.setattr(r, f, lambda *a: None)
    monkeypatch.setattr(r, "_blocos_relatorio", lambda *a: [])
    monkeypatch.setattr(r, "_enviar_blocos", lambda *a, **k: True)
    return r.enviar_relatorio_cliente("2026-11-17", "source_of", "meta_ads"), postou


def test_dado_atrasado_retem_com_alerta(r, monkeypatch):
    ok, postou = _segmento(r, monkeypatch, None, "2026-11-15", ("ok", ""))
    assert not ok and not postou and r._alertas_teste == ["relatorio_retido:source_of:meta_ads:2026-11-17"]


def test_dia_sem_gasto_com_fonte_em_dia_nao_alerta(r, monkeypatch):
    ok, postou = _segmento(r, monkeypatch, None, "2026-11-17", ("ok", ""))
    assert not ok and not postou and r._alertas_teste == []


@pytest.mark.parametrize("conferencia,env,sai", [
    (("sem_conferencia", "API indisponível (timeout)"), None, False),
    (("sem_conferencia", "API indisponível (timeout)"), "enviar", True),
    (("sem_conferencia", "conta não configurada"), None, True),
    (("divergente", "banco R$ 1 × API R$ 2"), None, False),
    (("ok", "conferido"), None, True),
])
def test_api_fora_retem(r, monkeypatch, conferencia, env, sai):
    if env:
        monkeypatch.setenv("RELATORIO_SEM_CONFERENCIA", env)
    else:
        monkeypatch.delenv("RELATORIO_SEM_CONFERENCIA", raising=False)
    ok, postou = _segmento(r, monkeypatch, {"investimento": 100}, "2026-11-17", conferencia)
    assert ok is sai and bool(postou) is sai and bool(r._alertas_teste) is not sai
