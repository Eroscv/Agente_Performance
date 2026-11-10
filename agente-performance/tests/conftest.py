"""
tests/conftest.py

Pré-requisito #1 da "Metodologia de Teste de Skills — Agente de Performance"
(Notion): fixture `fixture_db` materializa SQLite sintéticos em `tmp_path`
— nunca os `.db` de `data/performance/` (que não existem neste clone e,
em produção, contêm dado real de cliente).

Dataset: 3 campanhas Source OF (Meta) + 1 Source OF (Google) + 2 campanhas
POD (Meta) + 1 POD (Google) + 1 Botica (Meta), cobrindo um período fixo e
conhecido — 2026-06-01 a 2026-06-07 (7 dias) — com os MESMOS valores
repetidos todo dia. Isso torna os totais triviais de verificar à mão:
investimento total da campanha "ABO - Conversao" = 100.0 (diário) × 7 = 700.0.
Shopify (receita + sessões) sintético só para Source OF, único cliente
com Shopify de verdade (ver agent/clients/source_of.py).

Por que isso importa (seção 1 da Metodologia): hoje a maior parte da
suíte (tests/test_consultas.py, test_dados_consultas.py, etc.) falha
neste clone não por bug de lógica, mas por `data/performance/*.db`
ausente — e os poucos testes que "passam" contra banco real em produção
fazem `assert investimento_total > 0`, o que não pega regressão de
cálculo. `fixture_db` resolve os dois problemas: roda sem dado de
produção E permite `assert investimento_total == 3150.0`.

Uso: declarar `fixture_db` como parâmetro de um teste — qualquer chamada
a `agent.queries.*` ou `db.connections.query_fed()` durante o teste lê
só este dado sintético conhecido, nunca `data/performance/` real.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

PERIODO_INICIO = date(2026, 6, 1)
PERIODO_DIAS = 7
PERIODO_FIM = PERIODO_INICIO + timedelta(days=PERIODO_DIAS - 1)  # 2026-06-07


def _datas() -> list[str]:
    return [(PERIODO_INICIO + timedelta(days=i)).isoformat() for i in range(PERIODO_DIAS)]


# ── Dataset sintético — valores DIÁRIOS fixos, repetidos todo dia ───────────
# (campanha, campaign_id, objetivo, status_criativo, impressoes, cliques_link,
#  alcance, investimento, compras, valor_compras, video_3s, thruplays)
SOURCE_OF_META = [
    ("ABO - Conversao",      "sof-meta-001", "CONVERSIONS", "ativo",   1000, 30, 800, 100.0, 2, 400.0, 50, 20),
    ("Advantage Hidratante", "sof-meta-002", "SALES",       "ativo",    800, 20, 600,  80.0, 1, 120.0, 30, 10),
    ("Retargeting Carrinho", "sof-meta-003", "CONVERSIONS", "pausado",  500, 15, 400,  50.0, 0,   0.0, 10,  5),
]

# (campanha, campaign_id, tipo_campanha, status, impressoes, cliques,
#  investimento, conversoes, valor_conversao, ctr, cpc_medio)
SOURCE_OF_GADS = [
    ("Search - Marca", "sof-gads-001", "SEARCH", "ENABLED", 300, 40, 30.0, 1, 90.0, 13.33, 0.75),
]

POD_META = [
    ("POD - Awareness",          "pod-meta-001", "AWARENESS",   "ativo", 600, 10, 500, 40.0, 0,   0.0, 20,  8),
    ("POD - Conversao Kombucha", "pod-meta-002", "CONVERSIONS", "ativo", 900, 35, 700, 90.0, 2, 270.0, 40, 15),
]

POD_GADS = [
    ("PMax - Kombucha", "pod-gads-001", "PERFORMANCE_MAX", "ENABLED", 400, 25, 35.0, 1, 56.0, 6.25, 1.4),
]

BOTICA_META = [
    ("Botica - Ofertas", "botica-meta-001", "SALES", "ativo", 300, 8, 250, 25.0, 1, 75.0, 5, 2),
]

# Shopify (Source OF): (receita, pedidos, novos_clientes, recompradores,
# ticket_medio, total_desconto, pedidos_acima_300) — fixo por dia
SHOPIFY_RECEITA_DIARIA = (500.0, 10, 4, 6, 50.0, 20.0, 1)
# (sessoes, sessoes_com_carrinho, sessoes_checkout, sessoes_compra,
#  taxa_conversao, is_bot_suspect) — fixo por dia
SHOPIFY_SESSOES_DIARIA = (200, 50, 20, 10, 5.0, 0)


def _popular_meta_ads(path: Path, tabela: str, campanhas: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(f"""
        CREATE TABLE {tabela} (
            data TEXT, campanha TEXT, campaign_id TEXT, objetivo TEXT, status_criativo TEXT,
            impressoes INTEGER, cliques_link INTEGER, alcance INTEGER, investimento REAL,
            compras INTEGER, valor_compras REAL, video_3s INTEGER, thruplays INTEGER
        )
    """)
    for d in _datas():
        for c in campanhas:
            conn.execute(
                f"INSERT INTO {tabela} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d, *c),
            )
    conn.commit()
    conn.close()


def _popular_gads(path: Path, tabela: str, campanhas: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(f"""
        CREATE TABLE {tabela} (
            data TEXT, campanha TEXT, campaign_id TEXT, tipo_campanha TEXT, status TEXT,
            impressoes INTEGER, cliques INTEGER, investimento REAL, conversoes INTEGER,
            valor_conversao REAL, ctr REAL, cpc_medio REAL
        )
    """)
    for d in _datas():
        for c in campanhas:
            conn.execute(
                f"INSERT INTO {tabela} VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (d, *c),
            )
    conn.commit()
    conn.close()


def _popular_shopify(path_receita: Path, path_sessoes: Path) -> None:
    path_receita.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path_receita)
    conn.execute("""
        CREATE TABLE shopify_receita_diaria (
            data TEXT, receita REAL, pedidos INTEGER, novos_clientes INTEGER,
            recompradores INTEGER, ticket_medio REAL, total_desconto REAL,
            pedidos_acima_300 INTEGER
        )
    """)
    for d in _datas():
        conn.execute(
            "INSERT INTO shopify_receita_diaria VALUES (?,?,?,?,?,?,?,?)",
            (d, *SHOPIFY_RECEITA_DIARIA),
        )
    conn.commit()
    conn.close()

    path_sessoes.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path_sessoes)
    conn.execute("""
        CREATE TABLE source_of_shopify_sessoes (
            data TEXT, sessoes INTEGER, sessoes_com_carrinho INTEGER,
            sessoes_checkout INTEGER, sessoes_compra INTEGER,
            taxa_conversao REAL, is_bot_suspect INTEGER
        )
    """)
    for d in _datas():
        conn.execute(
            "INSERT INTO source_of_shopify_sessoes VALUES (?,?,?,?,?,?,?)",
            (d, *SHOPIFY_SESSOES_DIARIA),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    """Materializa os 7 SQLites sintéticos em tmp_path, reaponta
    db.duck._SQLITE_DBS/WH_DIR pra eles, e força a recriação da conexão
    DuckDB singleton. Devolve um dict com o período e os datasets, pra
    testes calcularem o total esperado sem hardcodar de novo.
    """
    import db.duck as duck

    base = tmp_path / "performance"
    paths = {
        "sof_meta":          base / "source_of" / "meta_ads.db",
        "sof_gads":          base / "source_of" / "google_ads.db",
        "pod_meta":          base / "pod_kombucha" / "meta_ads.db",
        "pod_gads":          base / "pod_kombucha" / "google_ads.db",
        "shopify_src":       base / "source_of" / "shopify_receita.db",
        "shopify_sess_src":  base / "source_of" / "shopify.db",
        "botica_meta":       base / "botica" / "meta_ads.db",
    }

    _popular_meta_ads(paths["sof_meta"], "source_of_meta_ads_diario", SOURCE_OF_META)
    _popular_gads(paths["sof_gads"], "source_of_gads_diario", SOURCE_OF_GADS)
    _popular_meta_ads(paths["pod_meta"], "pod_kombucha_meta_ads_diario", POD_META)
    _popular_gads(paths["pod_gads"], "pod_kombucha_gads_diario", POD_GADS)
    _popular_meta_ads(paths["botica_meta"], "botica_meta_ads_diario", BOTICA_META)
    _popular_shopify(paths["shopify_src"], paths["shopify_sess_src"])

    monkeypatch.setattr(duck, "_SQLITE_DBS", paths)
    # Aponta pra um diretório sem parquet — views dw_* ficam ausentes
    # (silencioso, ver db/duck.py::_criar_views_dw), não interfere no teste.
    monkeypatch.setattr(duck, "WH_DIR", tmp_path / "warehouse_vazio")
    duck.reset_duck_conn()

    yield {
        "periodo_inicio": PERIODO_INICIO.isoformat(),
        "periodo_fim": PERIODO_FIM.isoformat(),
        "dias": PERIODO_DIAS,
        "source_of_meta": SOURCE_OF_META,
        "source_of_gads": SOURCE_OF_GADS,
        "pod_meta": POD_META,
        "pod_gads": POD_GADS,
        "botica_meta": BOTICA_META,
    }

    duck.reset_duck_conn()  # próximo teste não deve herdar esta conexão/paths


# ── Unidade × integração ─────────────────────────────────────────────────────
# Estes arquivos leem data/performance/*.db reais (ausentes fora da VM). Por padrão ficam fora;
# `pytest -m integracao` roda só eles. ponytail: marcação por arquivo; migrar cada um para
# fixture_db e tirar da lista.
_INTEGRACAO = {"test_consultas.py", "test_infraestrutura.py", "test_duck_connection.py",
               "test_metricas.py", "test_dados_consultas.py", "test_engine_validacao.py"}


_INTEGRACAO_TESTES = {"test_resumo_geral_tem_delta"}          # lê v_performance real
_CASSETTE_VELHO = {"test_interpretar_top_roas_pod_google_via_cassette"}  # prompt mudou após a gravação


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.path.name in _INTEGRACAO or item.name in _INTEGRACAO_TESTES:
            item.add_marker(pytest.mark.integracao)
        if item.name in _CASSETTE_VELHO:
            item.add_marker(pytest.mark.xfail(reason="regravar o cassette com GEMINI_API_KEY real", strict=False))
