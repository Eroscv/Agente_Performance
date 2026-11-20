"""Regressões da sprint de confiabilidade (nov/2026): cada teste trava um defeito que chegou ao Slack."""
import inspect
from datetime import date

import duckdb
import pytest


def test_bigquery_so_com_opt_in(monkeypatch, tmp_path):
    """A credencial do GCP (transcrição/backup) desviava relatórios e bot para o BigQuery: 0/8 relatórios."""
    from db import bq_reader
    cred = tmp_path / "gcp.json"
    cred.write_text("{}")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(cred))
    monkeypatch.delenv("BQ_LEITURA", raising=False)
    assert bq_reader.enabled() is False
    monkeypatch.setenv("BQ_LEITURA", "1")
    assert bq_reader.enabled() is True


def test_v_performance_uma_linha_por_campanha():
    """Meta grava por anúncio; o relatório listava a mesma campanha 3-4 vezes."""
    from db.duck import _V_PERFORMANCE_AGG_DDL
    c = duckdb.connect()
    c.execute("""CREATE TABLE v_performance_linhas AS SELECT * FROM (VALUES
        ('source_of','Source OF','meta_ads','2026-11-12','Camp A','1','vendas','ACTIVE',1000.0,20.0,800.0,100.0,2.0,600.0,NULL,NULL,NULL,NULL,0.0,0.0),
        ('source_of','Source OF','meta_ads','2026-11-12','Camp A','1','vendas','ACTIVE',1000.0,30.0,900.0,100.0,3.0,400.0,NULL,NULL,NULL,NULL,0.0,0.0),
        ('source_of','Source OF','google_ads','2026-11-12','Camp G','2','search',NULL,500.0,10.0,NULL,50.0,1.0,200.0,NULL,NULL,NULL,NULL,NULL,NULL)
        ) t(cliente_key,cliente_nome,plataforma,data,campanha,campaign_id,objetivo,status,impressoes,cliques,alcance,
            investimento,conversoes,receita,roas,cpa,ctr,cpc_medio,video_3s,thruplays)""")
    c.execute(_V_PERFORMANCE_AGG_DDL)
    rows = c.execute("SELECT campanha, investimento, receita, roas, cpc_medio FROM v_performance ORDER BY campanha").fetchall()
    assert [r[0] for r in rows] == ["Camp A", "Camp G"]
    camp_a = rows[0]
    assert camp_a[1:3] == (200.0, 1000.0)
    assert camp_a[3] == round(1000 / (200 * 1.1215), 4)  # Meta da Source OF: ROAS sobre investimento com imposto
    assert camp_a[4] == 4.0                               # CPC = 200 / 50 cliques
    assert rows[1][3] == 4.0                               # Google sem imposto


def test_crons_por_cliente_chamam_o_relatorio_sem_rotulos():
    """pod/cron.py e source_of/cron.py não passavam periodo_str/tipo_label/tipo_builder: TypeError diário."""
    from scripts.cron_todos_relatorios import executar_cliente, executar_cliente_multi_plataforma, rotulos
    kw = dict(cfg={}, cliente_key="pod_kombucha", data_inicio=date(2026, 11, 12), data_fim=date(2026, 11, 12),
              periodo="diario", publicar=True, apenas_alertas=False)
    for f in (executar_cliente, executar_cliente_multi_plataforma):
        inspect.signature(f).bind(**kw)
    assert rotulos("diario", date(2026, 11, 12), date(2026, 11, 12)) == ("12/11/2026 → 12/11/2026", "Diário", "diario")


@pytest.mark.parametrize("api,esperado", [(1000.40, "ok"), (1200.0, "divergente"), (RuntimeError("timeout"), "sem_conferencia")])
def test_relatorio_confere_investimento_com_a_api(monkeypatch, api, esperado):
    """Relatório só sai com o investimento batendo com a API; divergente é retido."""
    from scripts import qualidade_dados as q
    from scripts import relatorio_diario as r
    monkeypatch.setattr(q, "contas", lambda: [{"cliente": "pod_kombucha", "plat": "meta_ads", "conta": "1", "token": "T"}])

    def _api(conta, token, ini, fim):
        if isinstance(api, Exception):
            raise api
        return {ini: api}
    monkeypatch.setattr(q, "_api_meta", _api)
    assert r.conferir_fonte("2026-11-12", "pod_kombucha", "meta_ads", 1000.0)[0] == esperado


def test_insights_descartam_numero_sem_origem():
    """O LLM escrevia números que não estavam nos dados (e o 'ROAS real' dividia a loja por uma plataforma)."""
    from relatorio.insights import _leituras, numeros_fora_dos_dados as fora
    assert _leituras("12.000") == {12.0, 12000.0} and _leituras("9,324.75") == {9324.75}
    entrada = "Investimento: R$ 9,324.75 | ROAS: 6.17x | Campanha Advantage_TodosProdutos_05/2026 | até 3 ações"
    assert fora("Escalar Advantage_TodosProdutos_05/2026: ROAS 6,2x com R$ 9.325 (9,3 mil). Até 3 ações.", entrada) == []
    assert fora("ROAS caiu 18% e o investimento foi R$ 12.000", entrada) == ["18", "12.000"]


def test_insights_sem_roas_da_loja_inteira():
    import relatorio.insights as ins
    src = inspect.getsource(ins.gerar_insights)
    assert "ROAS real (Shopify" not in src and "loja inteira" in src


@pytest.mark.parametrize("view_dia12,esperado", [
    ({"investimento": 1000.0, "compras": 20, "receita": 5000.0}, []),
    ({"investimento": 2000.0, "compras": 20, "receita": 5000.0},
     ["pod_kombucha/meta_ads 2026-11-12: investimento no relatório R$ 2.000,00 × API R$ 1.000,00"]),
    ({"investimento": 1000.0, "compras": 21, "receita": 5040.0}, []),  # dentro da tolerância (2 pedidos; R$ 50)
])
def test_teste_ouro_confere_a_view_dos_relatorios(monkeypatch, view_dia12, esperado):
    """SQLite certo e view errada (campanha duplicada) passava pela conferência; agora não passa.
    Compras e receita também são conferidas (eram o numerador do ROAS e ficavam de fora)."""
    from scripts import qualidade_dados as q
    monkeypatch.setattr(q, "contas", lambda: [
        {"cliente": "pod_kombucha", "plat": "meta_ads", "conta": c, "token": "T", "db": "/x.db", "tabela": "t"} for c in ("1", "2")])
    metade = lambda conta: 0.6 if conta == "1" else 0.4
    monkeypatch.setattr(q, "_api_meta_dias", lambda conta, *a: {"2026-11-12": {
        "investimento": 1000.0 * metade(conta), "compras": 20 * metade(conta), "receita": 5000.0 * metade(conta)}})
    monkeypatch.setattr(q, "_local", lambda db, tab, ini, fim, conta: {"2026-11-12": 1000.0 * metade(conta)})
    monkeypatch.setattr(q, "_duplicatas", lambda *a: 0)
    monkeypatch.setattr(q, "_view", lambda *a: {m: {"2026-11-12": v} for m, v in view_dia12.items()})
    assert q.verificar(date(2026, 11, 12), date(2026, 11, 12)) == esperado


def test_compras_e_receita_ignoram_o_ultimo_dia_ainda_em_atribuicao(monkeypatch):
    """AUD-04: o ROAS enviado ao cliente. O último dia fica de fora de compras/receita (atribuição em curso)."""
    from scripts import qualidade_dados as q
    monkeypatch.setattr(q, "contas", lambda: [
        {"cliente": "pod_kombucha", "plat": "meta_ads", "conta": "1", "token": "T", "db": "/x.db", "tabela": "t"}])
    api = {d: {"investimento": 1000.0, "compras": 20, "receita": 5000.0} for d in ("2026-11-11", "2026-11-12")}
    monkeypatch.setattr(q, "_api_meta_dias", lambda *a: api)
    monkeypatch.setattr(q, "_local", lambda *a: {d: 1000.0 for d in api})
    monkeypatch.setattr(q, "_duplicatas", lambda *a: 0)
    vis = {"investimento": {d: 1000.0 for d in api}, "compras": {"2026-11-11": 25, "2026-11-12": 25},
           "receita": {"2026-11-11": 5300.0, "2026-11-12": 5300.0}}
    monkeypatch.setattr(q, "_view", lambda *a: vis)
    assert q.verificar(date(2026, 11, 11), date(2026, 11, 12)) == [
        "pod_kombucha/meta_ads 2026-11-11: compras no relatório 25 × API 20",
        "pod_kombucha/meta_ads 2026-11-11: receita no relatório R$ 5.300,00 × API R$ 5.000,00"]


def test_api_meta_usa_a_janela_e_o_parser_do_sync(monkeypatch):
    """A conta inteira na API, lida com a mesma regra de compra do sync (7d_click + 1d_view do pixel)."""
    from scripts import qualidade_dados as q
    pedido = {}

    class R:
        def raise_for_status(self): pass
        def json(self): return {"data": [{"date_start": "2026-11-12", "spend": "100.5",
            "actions": [{"action_type": "offsite_conversion.fb_pixel_purchase", "7d_click": "3", "1d_view": "1"}],
            "action_values": [{"action_type": "offsite_conversion.fb_pixel_purchase", "7d_click": "300", "1d_view": "50"}]}]}
    monkeypatch.setenv("T", "x")
    monkeypatch.setattr(q.requests, "get", lambda url, timeout, params: (pedido.update(params), R())[1])
    assert q._api_meta_dias("1", "T", "2026-11-12", "2026-11-12") == {
        "2026-11-12": {"investimento": 100.5, "compras": 4.0, "receita": 350.0}}
    assert "7d_click" in pedido["action_attribution_windows"] and "action_values" in pedido["fields"]
    assert q._api_meta("1", "T", "2026-11-12", "2026-11-12") == {"2026-11-12": 100.5}


def test_saude_verde_e_vermelho():
    from scripts.saude import _nome, _quando, montar
    assert _nome("guara-job@relatorio_diario.service") == "relatorio_diario" and _nome("guara-transcricao.service") == "transcricao"
    assert _quando("Fri 2026-11-13 13:00:35 UTC") == "13/11 13:00 UTC"
    ok = {"rodou": True, "Result": "success", "ExecMainStatus": "0", "ExecMainExitTimestamp": "Fri 2026-11-13 13:00:35 UTC"}
    rot = [{"nome": "relatorio_diario", **ok}, {"nome": "qualidade_dados", **ok},
           {"nome": "sync_meta_semanal", "rodou": False, "Result": "", "ExecMainStatus": "", "ExecMainExitTimestamp": ""}]
    texto, prob = montar("13/11", rot, [], 551, 38.0)
    assert prob == [] and texto.startswith(":large_green_circle:") and "2 de 3 ok" in texto and "Teste-ouro (banco e relatórios × API): ok" in texto
    rot[1] = {**rot[1], "Result": "exit-code", "ExecMainStatus": "1"}
    texto, prob = montar("13/11", rot, ["pod_kombucha: Meta parado em 2026-11-10 (esperado ≥ 2026-11-12)"], 120, 38.0)
    assert len(prob) == 3 and texto.startswith(":red_circle:") and "*divergência*" in texto
    assert "rotina *qualidade_dados* falhou (código 1) em 13/11 13:00 UTC" in texto


def test_saude_swap_e_rptn(tmp_path):
    """AUD-05 (swap) e AUD-13 (RPTN parado ou com cliente preso em erro) no post diário."""
    import os, sqlite3, time
    from scripts.saude import montar, rptn
    texto, prob = montar("17/11", [], [], 551, 38.0, swap_mb=409)
    assert prob == ["VM usando swap: 409 MB (máximo 300); falta memória para algum serviço"] and "swap 409 MB" in texto

    (tmp_path / "reports").mkdir()
    log = tmp_path / "reports" / "sync_loop.log"
    log.write_text("x")
    with sqlite3.connect(tmp_path / "leads_reportana.db") as c:
        c.execute("CREATE TABLE seg_rptn_new (shopify_customer_id TEXT, status TEXT, erro TEXT)")
        c.execute("CREATE TABLE seg_last_edit (segment_id TEXT)")
        c.executemany("INSERT INTO seg_rptn_new VALUES (?, ?, ?)", [("1", "enviado", None), ("2", "erro", "HTTP 422")])
    agora = time.time()
    assert rptn(tmp_path, "active", agora) == ["RPTN: 1 cliente(s) presos em erro em seg_rptn_new (ex.: HTTP 422)"]
    with sqlite3.connect(tmp_path / "leads_reportana.db") as c:
        c.execute("UPDATE seg_rptn_new SET status='enviado'")
    assert rptn(tmp_path, "active", agora) == []
    os.utime(log, (agora - 9 * 3600, agora - 9 * 3600))
    assert rptn(tmp_path, "active", agora) == ["RPTN: nenhum ciclo registrado há 9 h"]
    assert rptn(tmp_path, "failed", agora) == ["RPTN: serviço guara-rptn failed"]
    assert rptn(tmp_path / "nao-existe") == []   # VM sem RPTN


def test_saude_rptn_sem_envio(tmp_path):
    """12-20/11/2026: o RPTN rodava todo ciclo sem enviar ninguém e a saúde só olhava status='erro'."""
    import sqlite3, time
    from datetime import datetime, timezone
    from scripts.saude import rptn
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "sync_loop.log").write_text("x")
    agora = datetime(2026, 11, 20, 18, tzinfo=timezone.utc).timestamp()
    with sqlite3.connect(tmp_path / "leads_reportana.db") as c:
        c.execute("CREATE TABLE seg_rptn_new (shopify_customer_id TEXT, status TEXT, erro TEXT, enviado_em TEXT)")
        c.execute("INSERT INTO seg_rptn_new VALUES ('1', 'enviado', NULL, '2026-11-12T10:00:00+00:00')")
    import os; os.utime(tmp_path / "reports" / "sync_loop.log", (agora, agora))
    assert rptn(tmp_path, "active", agora) == ["RPTN: nenhum cliente enviado à Reportana há 8 dias (último em 2026-11-12)"]
    with sqlite3.connect(tmp_path / "leads_reportana.db") as c:
        c.execute("UPDATE seg_rptn_new SET enviado_em='2026-11-19T10:00:00+00:00'")
    assert rptn(tmp_path, "active", agora) == []
