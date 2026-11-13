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
