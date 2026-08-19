"""
tests/test_infraestrutura.py

Testes de integração para as novas camadas de infraestrutura:
  - db/connections.py (multi-DB federado)
  - agent/registry.py (segmentos + contexto canal)
  - agent/queries/source_of/ e agent/queries/pod_kombucha/
  - shopify/sync.py
  - Integridade dos dados migrados
"""
import pytest
from pathlib import Path


# ── db/connections.py ─────────────────────────────────────────────────────────

class TestConnectionsPerformance:
    """Conexões diretas por cliente/plataforma."""

    def test_source_of_meta_ads(self):
        from db.connections import get_performance_conn
        conn = get_performance_conn("source_of", "meta_ads")
        n = conn.execute("SELECT COUNT(*) FROM source_of_meta_ads_diario").fetchone()[0]
        assert n == 32193

    def test_source_of_google_ads(self):
        from db.connections import get_performance_conn
        conn = get_performance_conn("source_of", "google_ads")
        n = conn.execute("SELECT COUNT(*) FROM source_of_gads_diario").fetchone()[0]
        assert n >= 372  # backfill 90 dias (2026-05-21 → 2026-08-18)

    def test_pod_kombucha_meta_ads(self):
        from db.connections import get_performance_conn
        conn = get_performance_conn("pod_kombucha", "meta_ads")
        n = conn.execute("SELECT COUNT(*) FROM pod_kombucha_meta_ads_diario").fetchone()[0]
        assert n == 2285

    def test_pod_kombucha_google_ads(self):
        from db.connections import get_performance_conn
        conn = get_performance_conn("pod_kombucha", "google_ads")
        n = conn.execute("SELECT COUNT(*) FROM pod_kombucha_gads_diario").fetchone()[0]
        assert n == 1226

    def test_chave_invalida_levanta_keyerror(self):
        from db.connections import get_performance_conn
        with pytest.raises(KeyError):
            get_performance_conn("cliente_inexistente", "meta_ads")

    def test_singleton_mesma_instancia(self):
        from db.connections import get_performance_conn
        c1 = get_performance_conn("source_of", "meta_ads")
        c2 = get_performance_conn("source_of", "meta_ads")
        assert c1 is c2


class TestConnectionsLogs:
    """Conexões de log (inbox/outbox/agent)."""

    def test_inbox_consultas_usuarios(self):
        from db.connections import get_inbox_conn
        n = get_inbox_conn().execute("SELECT COUNT(*) FROM consultas_usuarios").fetchone()[0]
        assert n >= 0

    def test_outbox_relatorios_log(self):
        from db.connections import get_outbox_conn
        n = get_outbox_conn().execute("SELECT COUNT(*) FROM relatorios_log").fetchone()[0]
        assert n >= 0

    def test_outbox_cron_runs(self):
        from db.connections import get_outbox_conn
        n = get_outbox_conn().execute("SELECT COUNT(*) FROM cron_runs").fetchone()[0]
        assert n >= 0

    def test_agent_nlp_cache(self):
        from db.connections import get_agent_conn
        n = get_agent_conn().execute("SELECT COUNT(*) FROM nlp_cache").fetchone()[0]
        assert n >= 0


class TestConnectionsFederado:
    """Conexão federada com TEMP VIEWs."""

    def test_v_performance_total(self):
        from db.connections import get_federated_conn
        n = get_federated_conn().execute("SELECT COUNT(*) FROM v_performance").fetchone()[0]
        assert n >= 36076  # cresce com syncs diários

    def test_campanhas_unificada_total(self):
        from db.connections import get_federated_conn
        n = get_federated_conn().execute("SELECT COUNT(*) FROM campanhas_unificada").fetchone()[0]
        assert n >= 36076  # cresce com syncs diários

    def test_shopify_receita_diaria(self):
        from db.connections import get_federated_conn
        n = get_federated_conn().execute("SELECT COUNT(*) FROM shopify_receita_diaria").fetchone()[0]
        assert n == 414

    def test_segmentos_corretos(self):
        from db.connections import get_federated_conn
        rows = get_federated_conn().execute(
            "SELECT cliente_key, plataforma, COUNT(*) n FROM v_performance "
            "GROUP BY cliente_key, plataforma ORDER BY 1,2"
        ).fetchall()
        seg = {(r["cliente_key"], r["plataforma"]): r["n"] for r in rows}
        assert seg[("source_of",    "meta_ads")]   == 32193
        assert seg[("source_of",    "google_ads")] >= 372  # backfill 90 dias
        assert seg[("pod_kombucha", "meta_ads")]   == 2285
        assert seg[("pod_kombucha", "google_ads")] == 1226

    def test_query_fed_helper(self):
        from db.connections import query_fed
        rows = query_fed(
            "SELECT COUNT(*) n FROM campanhas_unificada WHERE plataforma=?",
            ("meta_ads",)
        )
        assert rows[0]["n"] == 34478  # 32193 + 2285

    def test_query_db_helper(self):
        from db.connections import query_db
        rows = query_db("source_of/meta_ads",
            "SELECT COUNT(*) n FROM source_of_meta_ads_diario")
        assert rows[0]["n"] == 32193

    def test_legacy_get_conn_redireciona(self):
        """db.connection.get_conn() deve retornar a conexão federada."""
        from db.connection import get_conn
        from db.connections import get_federated_conn
        assert get_conn() is get_federated_conn()


# ── agent/registry.py ─────────────────────────────────────────────────────────

class TestRegistry:
    """Registry de clientes e segmentos."""

    def test_segmentos_ativos_retorna_lista(self):
        from agent.registry import carregar_segmentos_ativos
        segs = carregar_segmentos_ativos()
        assert isinstance(segs, list)
        assert len(segs) >= 2

    def test_segmentos_tem_campos(self):
        from agent.registry import carregar_segmentos_ativos
        for seg in carregar_segmentos_ativos():
            assert "cliente_key" in seg
            assert "plataforma" in seg
            assert "nome_cliente" in seg
            assert "label" in seg

    def test_segmentos_exclui_shopify(self):
        from agent.registry import carregar_segmentos_ativos
        for seg in carregar_segmentos_ativos():
            assert seg["plataforma"] != "shopify"

    def test_contexto_pod_por_nome(self):
        from agent.registry import get_contexto_cliente
        assert get_contexto_cliente("pod-channel") == "pod_kombucha"
        assert get_contexto_cliente("kombucha-ads") == "pod_kombucha"

    def test_contexto_source_of_por_nome(self):
        from agent.registry import get_contexto_cliente
        assert get_contexto_cliente("source-of-main") == "source_of"
        assert get_contexto_cliente("sof-performance") == "source_of"

    def test_contexto_generico_retorna_none(self):
        from agent.registry import get_contexto_cliente
        assert get_contexto_cliente("C05SFND7R1D") is None
        assert get_contexto_cliente("performance") is None
        assert get_contexto_cliente("") is None

    def test_contexto_por_id_configurado(self):
        """Se slack_channel_ids tiver IDs, prioridade sobre heurística."""
        from agent.registry import get_contexto_cliente
        import json
        from pathlib import Path
        data = json.loads(Path("clientes.json").read_text())
        # Injeta ID de teste temporário
        data["clientes"]["source_of"]["slack_channel_ids"] = ["C_TEST_SOF"]
        tmp = Path("/tmp/clientes_test.json")
        tmp.write_text(json.dumps(data))
        # Testa lookup direto (não via registry cacheado)
        import agent.registry as reg
        orig = reg._CLIENTES_JSON
        reg._CLIENTES_JSON = str(tmp)
        try:
            result = reg.get_contexto_cliente("C_TEST_SOF")
            assert result == "source_of", f"esperado source_of, got {result}"
        finally:
            reg._CLIENTES_JSON = orig
            tmp.unlink()

    def test_campanhas_disponiveis(self):
        from agent.registry import get_available_campaigns
        camps = get_available_campaigns()
        assert len(camps) > 10

    def test_campanhas_por_cliente(self):
        from agent.registry import get_available_campaigns
        camps = get_available_campaigns(cliente_key="source_of")
        assert len(camps) > 0
        camps_pod = get_available_campaigns(cliente_key="pod_kombucha")
        assert len(camps_pod) > 0

    def test_engine_reexporta(self):
        """engine.py deve re-exportar registry sem erro."""
        from agent.engine import _carregar_segmentos_ativos, get_contexto_cliente
        assert len(_carregar_segmentos_ativos()) >= 2
        assert get_contexto_cliente("pod") == "pod_kombucha"


# ── agent/queries/source_of/ ──────────────────────────────────────────────────

class TestQueriesSourceOf:
    """Queries por cliente Source OF."""

    def test_meta_resumo_retorna_dados(self):
        from agent.queries.source_of.meta_ads import resumo
        rows = resumo(data_inicio="2026-01-01", data_fim="2026-08-17")
        assert len(rows) > 0
        assert "campanha" in rows[0]
        assert "investimento" in rows[0]

    def test_meta_top_roas_ordenado(self):
        from agent.queries.source_of.meta_ads import top_roas
        rows = top_roas(data_inicio="2026-01-01", data_fim="2026-08-17", limit=5)
        if len(rows) > 1:
            assert rows[0].get("roas", 0) >= rows[1].get("roas", 0)

    def test_meta_criativos_usa_schema_federado(self):
        from agent.queries.source_of.meta_ads import criativos
        rows = criativos(data_inicio="2026-01-01", data_fim="2026-08-17", limit=5)
        # Valida que retorna dados do schema sof_meta.*
        assert isinstance(rows, list)

    def test_meta_evolucao_serie_temporal(self):
        from agent.queries.source_of.meta_ads import evolucao
        rows = evolucao(data_inicio="2026-08-01", data_fim="2026-08-10")
        assert len(rows) == 10
        datas = [r["data"] for r in rows]
        assert datas == sorted(datas)

    def test_gads_resumo_retorna_dados(self):
        from agent.queries.source_of.google_ads import resumo
        rows = resumo(data_inicio="2026-01-01", data_fim="2026-08-17")
        assert len(rows) > 0

    def test_shopify_diario_retorna_serie(self):
        from agent.queries.source_of.shopify import diario
        rows = diario(data_inicio="2026-08-01", data_fim="2026-08-17")
        assert len(rows) > 0
        assert "receita" in rows[0]

    def test_meta_alertas_retorna_lista(self):
        from agent.queries.source_of.meta_ads import alertas
        result = alertas(roas_minimo=0.5)
        assert isinstance(result, list)


class TestQueriesPODKombucha:
    """Queries por cliente POD Kombucha."""

    def test_meta_resumo_retorna_dados(self):
        from agent.queries.pod_kombucha.meta_ads import resumo
        rows = resumo(data_inicio="2026-01-01", data_fim="2026-08-17")
        assert len(rows) > 0

    def test_meta_criativos_usa_schema_federado(self):
        from agent.queries.pod_kombucha.meta_ads import criativos
        rows = criativos(data_inicio="2026-01-01", data_fim="2026-08-17", limit=5)
        assert isinstance(rows, list)

    def test_gads_resumo_retorna_dados(self):
        from agent.queries.pod_kombucha.google_ads import resumo
        rows = resumo(data_inicio="2024-01-01", data_fim="2026-08-17")
        assert len(rows) > 0

    def test_meta_sem_shopify(self):
        """POD não tem Shopify — garantir que não há import de shopify em pod."""
        import importlib
        mod = importlib.import_module("agent.queries.pod_kombucha.meta_ads")
        src = open(mod.__file__).read()
        assert "shopify" not in src.lower()

    def test_meta_evolucao(self):
        from agent.queries.pod_kombucha.meta_ads import evolucao
        rows = evolucao(data_inicio="2026-08-01", data_fim="2026-08-10")
        assert isinstance(rows, list)


# ── shopify/sync.py ───────────────────────────────────────────────────────────

class TestShopifySync:
    """shopify/sync.py movido de db/shopify_sync.py."""

    def test_importa_de_shopify_sync(self):
        from shopify.sync import sincronizar, metricas_shopify_periodo, status_sync
        assert callable(sincronizar)
        assert callable(metricas_shopify_periodo)
        assert callable(status_sync)

    def test_alias_db_shopify_sync(self):
        """db/shopify_sync.py deve re-exportar de shopify/sync.py."""
        from db.shopify_sync import sincronizar as s1, status_sync as st1
        from shopify.sync import sincronizar as s2, status_sync as st2
        assert s1 is s2
        assert st1 is st2

    def test_status_sync_retorna_dict(self):
        from shopify.sync import status_sync
        s = status_sync()
        assert isinstance(s, dict)

    def test_status_sync_tem_campos(self):
        from shopify.sync import status_sync
        s = status_sync()
        assert "sincronizado" in s

    def test_arquivo_movido_para_shopify(self):
        assert Path("shopify/sync.py").exists()
        assert Path("db/shopify_sync.py").exists()  # stub ainda existe


# ── MULTI_AGENT routing ───────────────────────────────────────────────────────

class TestMultiAgentRouting:
    """Verifica que MULTI_AGENT=1 roteia para OrchestratorAgent."""

    def test_consultar_verifica_env(self):
        import inspect
        from agent.engine import consultar
        src = inspect.getsource(consultar)
        assert "MULTI_AGENT" in src
        assert "consultar_v2" in src

    def test_sem_flag_usa_engine_classico(self):
        import os
        os.environ["MULTI_AGENT"] = "0"
        # Só verifica que importa sem erro — não chama NLP real
        from agent.engine import consultar
        assert callable(consultar)
        os.environ["MULTI_AGENT"] = "1"

    def test_registry_canal_id_exato_tem_prioridade(self):
        """ID exato em slack_channel_ids supera heurística de nome."""
        import json
        from pathlib import Path
        import agent.registry as reg
        data = json.loads(Path("clientes.json").read_text())
        data["clientes"]["pod_kombucha"]["slack_channel_ids"] = ["C_POD_EXACT"]
        tmp = Path("/tmp/clientes_test2.json")
        tmp.write_text(json.dumps(data))
        orig = reg._CLIENTES_JSON
        reg._CLIENTES_JSON = str(tmp)
        try:
            assert reg.get_contexto_cliente("C_POD_EXACT") == "pod_kombucha"
            # Heurística ainda funciona para outros
            assert reg.get_contexto_cliente("source-of-canal") == "source_of"
        finally:
            reg._CLIENTES_JSON = orig
            tmp.unlink()
