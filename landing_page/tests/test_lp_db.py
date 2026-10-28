"""
landing_page/tests/test_lp_db.py
Testes para landing_page/db/ — database.py e queries.py.
25+ casos.
"""
import sys, os, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from pathlib import Path
from unittest.mock import patch

# Redireciona DB para temporário
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()

import landing_page.db.database as _dbmod
_dbmod.DB_PATH = Path(_tmp.name)
_dbmod._conn = None

import landing_page.db.queries as Q


@pytest.fixture(autouse=True)
def clean_db():
    _dbmod._conn = None
    Path(_tmp.name).unlink(missing_ok=True)
    yield
    _dbmod._conn = None


# ── Schema ────────────────────────────────────────────────────────────────────

def test_01_tabelas_criadas():
    c = _dbmod.conn()
    tabelas = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    for t in ("projetos", "mensagens", "preferencias", "refs_visuais", "sessoes"):
        assert t in tabelas, f"Tabela '{t}' não criada"

def test_02_schema_projetos_tem_html_content():
    c = _dbmod.conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(projetos)").fetchall()}
    assert "html_content" in cols
    assert "job_id"       in cols

def test_03_schema_refs_tem_ativo():
    c = _dbmod.conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(refs_visuais)").fetchall()}
    assert "ativo" in cols

# ── Sessões ───────────────────────────────────────────────────────────────────

def test_04_abrir_sessao_retorna_uuid():
    sid = Q.abrir_sessao("U1")
    assert len(sid) == 36 and "-" in sid  # UUID format

def test_05_abrir_sessao_cria_registro():
    sid = Q.abrir_sessao("U2")
    row = _dbmod.conn().execute("SELECT * FROM sessoes WHERE sessao_id=?", (sid,)).fetchone()
    assert row is not None
    assert row["user_id"] == "U2"

def test_06_encerrar_sessao():
    sid = Q.abrir_sessao("U3")
    Q.encerrar_sessao(sid, "gerado", "gerado")
    row = _dbmod.conn().execute("SELECT resultado FROM sessoes WHERE sessao_id=?", (sid,)).fetchone()
    assert row["resultado"] == "gerado"

# ── Mensagens ─────────────────────────────────────────────────────────────────

def test_07_salvar_mensagem_user():
    sid = Q.abrir_sessao("U4")
    Q.salvar_mensagem("U4", sid, "user", "Quero criar uma LP")
    rows = Q.historico_recente("U4")
    assert len(rows) == 1
    assert rows[0]["papel"] == "user"

def test_08_historico_ordem_cronologica():
    sid = Q.abrir_sessao("U5")
    Q.salvar_mensagem("U5", sid, "user", "Msg 1")
    Q.salvar_mensagem("U5", sid, "agent", "Resp 1")
    Q.salvar_mensagem("U5", sid, "user", "Msg 2")
    hist = Q.historico_recente("U5")
    assert hist[-1]["texto"] == "Msg 2"  # mais recente no final

def test_09_historico_limite():
    sid = Q.abrir_sessao("U6")
    for i in range(25):
        Q.salvar_mensagem("U6", sid, "user", f"Msg {i}")
    hist = Q.historico_recente("U6", limite=10)
    assert len(hist) == 10

def test_10_historico_apenas_papel_valido():
    sid = Q.abrir_sessao("U7")
    # Papel inválido deve falhar
    with pytest.raises(Exception):
        _dbmod.conn().execute(
            "INSERT INTO mensagens (user_id, sessao_id, papel, texto) VALUES (?,?,?,?)",
            ("U7", sid, "invalido", "texto")
        )
        _dbmod.conn().commit()

# ── Projetos ──────────────────────────────────────────────────────────────────

def test_11_salvar_projeto_retorna_id():
    pid = Q.salvar_projeto("U8", "Source OF", "venda", "jovens", "direto")
    assert isinstance(pid, int) and pid > 0

def test_12_buscar_projeto():
    pid = Q.salvar_projeto("U9", "POD", "captação", "adultos", "empático")
    p = Q.buscar_projeto(pid)
    assert p["cliente"] == "POD"
    assert p["objetivo"] == "captação"

def test_13_buscar_projeto_inexistente():
    assert Q.buscar_projeto(999999) is None

def test_14_listar_projetos_por_usuario():
    Q.salvar_projeto("UA", "Cliente A", "obj", "pub", "tom")
    Q.salvar_projeto("UA", "Cliente B", "obj", "pub", "tom")
    Q.salvar_projeto("UB", "Cliente C", "obj", "pub", "tom")
    lista = Q.listar_projetos("UA")
    assert len(lista) == 2

def test_15_listar_projetos_limite():
    for i in range(12):
        Q.salvar_projeto("UC", f"Cliente {i}", "obj", "pub", "tom")
    lista = Q.listar_projetos("UC", limite=5)
    assert len(lista) == 5

def test_16_salvar_e_recuperar_html():
    pid = Q.salvar_projeto("UD", "CLI", "obj", "pub", "tom")
    html = "<html><body>LP</body></html>"
    Q.salvar_html(pid, html)
    assert Q.get_html(pid) == html

def test_17_get_html_inexistente():
    assert Q.get_html(999999) is None

def test_18_atualizar_status_projeto():
    pid = Q.salvar_projeto("UE", "CLI", "obj", "pub", "tom")
    Q.atualizar_status_projeto(pid, "falha", "Timeout Claude")
    p = Q.buscar_projeto(pid)
    assert p["status"] == "falha"

# ── Preferências ──────────────────────────────────────────────────────────────

def test_19_get_preferencias_vazio():
    pref = Q.get_preferencias("U_NEW")
    assert pref == {}

def test_20_upsert_preferencias_cria():
    Q.upsert_preferencias("UF", tom_favorito="direto", cliente_padrao="Source OF")
    pref = Q.get_preferencias("UF")
    assert pref["tom_favorito"] == "direto"

def test_21_upsert_preferencias_atualiza():
    Q.upsert_preferencias("UG", tom_favorito="v1")
    Q.upsert_preferencias("UG", tom_favorito="v2")
    pref = Q.get_preferencias("UG")
    assert pref["tom_favorito"] == "v2"

# ── Referências visuais ───────────────────────────────────────────────────────

def test_22_salvar_e_buscar_ref_boa():
    Q.salvar_ref("UH", "boa", "Apple.com — clean")
    refs = Q.get_refs("UH", "boa")
    assert len(refs) == 1
    assert refs[0]["descricao"] == "Apple.com — clean"

def test_23_salvar_e_buscar_ref_ruim():
    Q.salvar_ref("UI", "ruim", "Fundo escuro, countdown fake")
    refs = Q.get_refs("UI", "ruim")
    assert len(refs) == 1

def test_24_refs_apenas_ativas():
    Q.salvar_ref("UJ", "boa", "Ref para desativar")
    ref_id = _dbmod.conn().execute(
        "SELECT id FROM refs_visuais WHERE user_id='UJ'"
    ).fetchone()[0]
    Q.desativar_ref(ref_id, "UJ")
    refs = Q.get_refs("UJ", "boa")
    assert len(refs) == 0

def test_25_desativar_ref_outro_usuario_falha():
    Q.salvar_ref("UK", "boa", "Ref do UK")
    ref_id = _dbmod.conn().execute(
        "SELECT id FROM refs_visuais WHERE user_id='UK'"
    ).fetchone()[0]
    result = Q.desativar_ref(ref_id, "OUTRO_USER")
    assert result is False
    # Ref ainda ativa
    assert len(Q.get_refs("UK", "boa")) == 1

def test_26_refs_como_texto():
    Q.salvar_ref("UL", "boa", "Apple.com")
    Q.salvar_ref("UL", "boa", "Notion.so")
    Q.salvar_ref("UL", "ruim", "Fundo escuro")
    boas, ruins = Q.refs_como_texto("UL")
    assert "Apple.com" in boas or "Notion.so" in boas
    assert "Fundo escuro" in ruins

def test_27_refs_como_texto_vazio():
    boas, ruins = Q.refs_como_texto("U_SEM_REFS")
    assert boas == ""
    assert ruins == ""

def test_28_refs_nao_mistura_usuarios():
    Q.salvar_ref("UM", "boa", "Ref do UM")
    Q.salvar_ref("UN", "boa", "Ref do UN")
    refs_m = Q.get_refs("UM", "boa")
    refs_n = Q.get_refs("UN", "boa")
    assert len(refs_m) == 1
    assert len(refs_n) == 1
    assert refs_m[0]["descricao"] == "Ref do UM"
