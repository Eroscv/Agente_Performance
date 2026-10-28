"""
tests/test_card_db.py
Testes para card_db.py — SQLite do agente /criar-card.
25+ casos cobrindo: schema, CRUD, cache, timeout, sessões.
"""
import sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import tempfile
from unittest.mock import patch
from pathlib import Path

# Redireciona DB para arquivo temporário
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()

import card_db
card_db.DB_PATH = Path(_tmp.name)
card_db._conn = None  # reseta singleton


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_db():
    """Recria banco limpo antes de cada teste."""
    card_db._conn = None
    Path(_tmp.name).unlink(missing_ok=True)
    yield
    card_db._conn = None


# ── 1. Schema / Inicialização ─────────────────────────────────────────────────

def test_01_banco_cria_tabelas():
    c = card_db.conn()
    tabelas = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "cards_criados"  in tabelas
    assert "cards_cache"    in tabelas
    assert "sessoes_card"   in tabelas

def test_02_banco_cria_indices():
    c = card_db.conn()
    indices = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_cards_user"   in indices
    assert "idx_cards_titulo" in indices

def test_03_singleton_retorna_mesma_conn():
    c1 = card_db.conn()
    c2 = card_db.conn()
    assert c1 is c2

# ── 2. Sessões ────────────────────────────────────────────────────────────────

def test_04_abrir_sessao():
    card_db.abrir_sessao("U001")
    row = card_db.conn().execute("SELECT * FROM sessoes_card WHERE user_id='U001'").fetchone()
    assert row is not None
    assert row["resultado"] is None
    assert row["step_atual"] == "cliente_card"

def test_05_abrir_sessao_replace():
    card_db.abrir_sessao("U001")
    card_db.abrir_sessao("U001")  # segunda abertura deve substituir
    rows = card_db.conn().execute("SELECT * FROM sessoes_card WHERE user_id='U001'").fetchall()
    assert len(rows) == 1

def test_06_atualizar_step():
    card_db.abrir_sessao("U002")
    card_db.atualizar_step("U002", "prazo")
    row = card_db.conn().execute("SELECT step_atual FROM sessoes_card WHERE user_id='U002'").fetchone()
    assert row["step_atual"] == "prazo"

def test_07_encerrar_sessao_criado():
    card_db.abrir_sessao("U003")
    card_db.encerrar_sessao("U003", "criado")
    row = card_db.conn().execute("SELECT resultado FROM sessoes_card WHERE user_id='U003'").fetchone()
    assert row["resultado"] == "criado"

def test_08_encerrar_sessao_nao_sobrescreve():
    card_db.abrir_sessao("U004")
    card_db.encerrar_sessao("U004", "cancelado")
    card_db.encerrar_sessao("U004", "criado")  # não deve sobrescrever
    row = card_db.conn().execute("SELECT resultado FROM sessoes_card WHERE user_id='U004'").fetchone()
    assert row["resultado"] == "cancelado"

def test_09_sessoes_timeout_detecta():
    card_db.conn().execute(
        "INSERT INTO sessoes_card (user_id, iniciada_em, step_atual) VALUES (?, datetime('now', '-2 hours'), 'prazo')",
        ("U_OLD",)
    )
    card_db.conn().commit()
    result = card_db.sessoes_timeout(minutos=60)
    assert "U_OLD" in result

def test_10_sessoes_timeout_ignora_recentes():
    card_db.abrir_sessao("U_NEW")
    result = card_db.sessoes_timeout(minutos=60)
    assert "U_NEW" not in result

def test_11_sessoes_timeout_ignora_encerradas():
    card_db.conn().execute(
        "INSERT INTO sessoes_card (user_id, iniciada_em, step_atual, resultado) VALUES (?, datetime('now', '-2 hours'), 'prazo', 'criado')",
        ("U_DONE",)
    )
    card_db.conn().commit()
    result = card_db.sessoes_timeout(minutos=60)
    assert "U_DONE" not in result

# ── 3. Cards Criados ──────────────────────────────────────────────────────────

def test_12_salvar_card_retorna_id():
    pid = card_db.salvar_card("U005", "Source OF: ADS - Campanha Black Friday")
    assert isinstance(pid, int)
    assert pid > 0

def test_13_salvar_card_campos_completos():
    card_db.salvar_card(
        "U006", "POD: Social - Reels de lançamento",
        cliente="POD", frente="Social", responsavel="Bianca",
        revisor="Daniel", prazo="2026-11-10",
        url_notion="https://notion.so/abc123",
        status="criado",
    )
    row = card_db.conn().execute("SELECT * FROM cards_criados WHERE user_id='U006'").fetchone()
    assert row["cliente"] == "POD"
    assert row["responsavel"] == "Bianca"
    assert row["url_notion"] == "https://notion.so/abc123"

def test_14_listar_cards_por_usuario():
    card_db.salvar_card("U007", "Titulo 1")
    card_db.salvar_card("U007", "Titulo 2")
    card_db.salvar_card("U999", "Outro usuario")
    resultado = card_db.listar_cards(user_id="U007")
    assert len(resultado) == 2
    assert all(c["user_id"] == "U007" for c in resultado)

def test_15_listar_cards_sem_filtro():
    card_db.salvar_card("UA", "Card A")
    card_db.salvar_card("UB", "Card B")
    resultado = card_db.listar_cards()
    assert len(resultado) >= 2

def test_16_listar_cards_limite():
    for i in range(15):
        card_db.salvar_card("U_LIMIT", f"Card {i}")
    resultado = card_db.listar_cards(user_id="U_LIMIT", limite=5)
    assert len(resultado) == 5

def test_17_listar_cards_ordem_desc():
    card_db.salvar_card("U_ORD", "Primeiro")
    card_db.salvar_card("U_ORD", "Segundo")
    resultado = card_db.listar_cards(user_id="U_ORD")
    assert resultado[0]["titulo"] == "Segundo"  # mais recente primeiro

def test_18_salvar_card_status_falha():
    card_db.salvar_card("U008", "Card com falha", status="falha")
    row = card_db.conn().execute("SELECT status FROM cards_criados WHERE user_id='U008'").fetchone()
    assert row["status"] == "falha"

# ── 4. Cache ──────────────────────────────────────────────────────────────────

def test_19_set_cache_e_get_cache():
    dados = [{"id": "page1", "titulo": "Test", "status": "A fazer"}]
    card_db.set_cache("uid:ABC123:abertos", dados)
    resultado = card_db.get_cache("uid:ABC123:abertos", ttl_minutos=5)
    assert resultado == dados

def test_20_get_cache_miss():
    resultado = card_db.get_cache("chave_inexistente")
    assert resultado is None

def test_21_get_cache_expirado():
    dados = [{"titulo": "Expirado"}]
    card_db.conn().execute(
        "INSERT OR REPLACE INTO cards_cache (cache_key, atualizado_em, payload_json) VALUES (?, datetime('now', '-10 minutes'), ?)",
        ("key_old", json.dumps(dados))
    )
    card_db.conn().commit()
    resultado = card_db.get_cache("key_old", ttl_minutos=5)
    assert resultado is None  # expirado

def test_22_set_cache_atualiza_existente():
    card_db.set_cache("key_upd", [{"v": 1}])
    card_db.set_cache("key_upd", [{"v": 2}])
    resultado = card_db.get_cache("key_upd")
    assert resultado == [{"v": 2}]

def test_23_invalidar_cache_limpa_tudo():
    card_db.set_cache("key1", [{"a": 1}])
    card_db.set_cache("key2", [{"b": 2}])
    card_db.invalidar_cache()
    assert card_db.get_cache("key1") is None
    assert card_db.get_cache("key2") is None

def test_24_invalidar_cache_por_usuario():
    card_db.set_cache("uid:USR1:abertos", [{"x": 1}])
    card_db.set_cache("all:abertos", [{"y": 2}])
    card_db.set_cache("uid:USR2:abertos", [{"z": 3}])
    card_db.invalidar_cache_por_usuario("USR1")
    assert card_db.get_cache("uid:USR1:abertos") is None
    assert card_db.get_cache("all:abertos") is None
    # USR2 não deve ser afetado
    assert card_db.get_cache("uid:USR2:abertos") is not None

def test_25_cache_serializa_unicode():
    dados = [{"titulo": "Ação de mídia pré-lançamento 🚀"}]
    card_db.set_cache("unicode_test", dados)
    resultado = card_db.get_cache("unicode_test")
    assert resultado[0]["titulo"] == "Ação de mídia pré-lançamento 🚀"

def test_26_multiplas_sessoes_independentes():
    """Múltiplos usuários em paralelo não se interferem."""
    for i in range(5):
        card_db.abrir_sessao(f"PARALLEL_{i}")
    for i in range(5):
        card_db.atualizar_step(f"PARALLEL_{i}", f"step_{i}")
    for i in range(5):
        row = card_db.conn().execute(
            "SELECT step_atual FROM sessoes_card WHERE user_id=?", (f"PARALLEL_{i}",)
        ).fetchone()
        assert row["step_atual"] == f"step_{i}"

def test_27_salvar_card_sem_campos_opcionais():
    """Card mínimo com apenas user_id e título."""
    pid = card_db.salvar_card("U_MIN", "Título mínimo")
    row = card_db.conn().execute("SELECT * FROM cards_criados WHERE id=?", (pid,)).fetchone()
    assert row["responsavel"] is None or row["responsavel"] == ""
    assert row["status"] == "criado"
