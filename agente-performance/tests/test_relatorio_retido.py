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


def test_ontem_primeiro_e_erro_num_segmento_nao_para_os_outros(r, monkeypatch):
    import agent.tz
    monkeypatch.setattr(agent.tz, "hoje_brt", lambda: date(2026, 11, 18))
    monkeypatch.setattr(r.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["relatorio_diario.py"])
    vistos = []

    def enviar(data, c, p):
        vistos.append((data, c, p))
        if len(vistos) == 1:
            raise RuntimeError("Slack recusou o alerta")
        return True
    monkeypatch.setattr(r, "enviar_relatorio_cliente", enviar)
    with pytest.raises(SystemExit) as e:
        r.main()
    assert e.value.code == 1 and vistos[0][0] == "2026-11-17" and len(vistos) > 1


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


def test_falha_depois_do_titulo_nao_reposta(r, monkeypatch):
    """Título já saiu no canal do cliente: falha na thread vira alerta interno e a chave fica gravada."""
    ok, postou = _segmento(r, monkeypatch, {"investimento": 100}, "2026-11-17", ("ok", ""))
    monkeypatch.setattr(r, "_enviar_blocos", lambda *a, **k: False)
    ok, postou = r.enviar_relatorio_cliente("2026-11-17", "source_of", "meta_ads"), postou
    assert ok is True and r._alertas_teste == ["relatorio_parcial:source_of:meta_ads:2026-11-17"]


def test_credencial_recusada_sai_com_selo_e_sem_token(monkeypatch):
    import requests
    from scripts import qualidade_dados as q
    from scripts import relatorio_diario as r
    monkeypatch.setattr(q, "contas", lambda: [{"cliente": "c", "plat": "meta_ads", "conta": "1", "token": "T"}])
    resp = requests.Response(); resp.status_code = 400
    erro = requests.HTTPError("400 Client Error for url: https://graph.facebook.com/v20.0/act_1/insights?access_token=SEGREDO&x=1",
                              response=resp)
    monkeypatch.setattr(q, "_api_meta", lambda *a: (_ for _ in ()).throw(erro))
    st, det = r.conferir_fonte("2026-11-17", "c", "meta_ads", 1.0)
    assert st == "sem_conferencia" and det.startswith("credencial") and "SEGREDO" not in det


def test_atraso_olha_a_plataforma_e_nao_o_cliente(r, monkeypatch):
    _segmento(r, monkeypatch, None, "2026-11-17", ("ok", ""))
    sqls = []
    monkeypatch.setattr(r, "query_one", lambda sql, args: (sqls.append((sql, args)), {"u": "2026-11-17"})[1])
    r.enviar_relatorio_cliente("2026-11-17", "pausado", "meta_ads")
    assert sqls[0][1] == ("meta_ads",) and "cliente_key" not in sqls[0][0]


def test_dado_atrasado_retem_com_alerta(r, monkeypatch):
    ok, postou = _segmento(r, monkeypatch, None, "2026-11-15", ("ok", ""))
    assert not ok and not postou and r._alertas_teste == ["relatorio_retido:source_of:meta_ads:2026-11-17"]


def test_dia_sem_gasto_com_fonte_em_dia_nao_alerta(r, monkeypatch):
    ok, postou = _segmento(r, monkeypatch, None, "2026-11-17", ("ok", ""))
    assert not ok and not postou and r._alertas_teste == []


@pytest.mark.parametrize("conferencia,env,sai", [
    (("sem_conferencia", "API indisponível (rede ou 5xx: timeout)"), None, False),
    (("sem_conferencia", "API indisponível (rede ou 5xx: timeout)"), "enviar", True),
    (("sem_conferencia", "credencial ou conta recusada (400)"), None, True),
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
