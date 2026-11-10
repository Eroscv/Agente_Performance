"""Garantias de idempotência e deduplicação dos processos (offline, sem rede)."""
import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]


# ── livro-razão de efeitos ────────────────────────────────────────────────────

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("IDEMPOTENCIA_DB", str(tmp_path / "idem.db"))
    import utils.idempotencia as m
    return importlib.reload(m)


def test_efeito_acontece_uma_vez(ledger):
    feitos = []
    for _ in range(3):
        with ledger.uma_vez("slack:rel:2026-11-09") as pode:
            if pode:
                feitos.append(1)
    assert feitos == [1] and ledger.ja_feito("slack:rel:2026-11-09")


def test_falha_libera_chave(ledger):
    with pytest.raises(RuntimeError):
        with ledger.uma_vez("k") as pode:
            assert pode
            raise RuntimeError("Slack fora")
    with ledger.uma_vez("k") as pode:
        assert pode  # nova tentativa permitida


def test_concorrente_nao_executa_em_dobro(ledger):
    with ledger.uma_vez("k") as a:
        with ledger.uma_vez("k") as b:  # segundo processo enquanto o primeiro está no meio
            assert a and not b


def test_reserva_orfa_expira(ledger):
    with sqlite3.connect(ledger.DB_PATH) as c:
        ledger._conn().close()
        c.execute("INSERT INTO efeitos VALUES ('k', 'em_andamento', '', '2000-01-01T00:00:00+00:00')")
    with ledger.uma_vez("k") as pode:
        assert pode


# ── Meta: grão (account_id, data, ad_id) ──────────────────────────────────────

def _linha(**kw):
    base = {"data": "2026-11-01", "campanha": "BF", "campaign_id": "c1", "ad_id": "a1",
            "account_id": "act_1", "investimento": 100.0}
    return {**base, **kw}


def _soma(db, tabela="t"):
    with sqlite3.connect(db) as c:
        return c.execute(f"SELECT COUNT(*), SUM(investimento) FROM {tabela}").fetchone()


def test_meta_rerun_e_rename_nao_duplicam(tmp_path):
    from meta_ads.loader import salvar
    db = tmp_path / "m.db"
    salvar([_linha()], append=True, tabela="t", db_path=db)
    salvar([_linha(investimento=120.0)], append=True, tabela="t", db_path=db)      # reprocesso com valor revisado
    salvar([_linha(campanha="BF renomeada")], append=True, tabela="t", db_path=db)  # campanha renomeada
    assert _soma(db) == (1, 100.0)


def test_meta_descarta_sem_ad_id_e_colapsa_lote(tmp_path):
    from meta_ads.loader import salvar
    db = tmp_path / "m.db"
    salvar([_linha(ad_id=None), _linha(), _linha(investimento=7.0), _linha(account_id="1", ad_id="a2")],
           append=True, tabela="t", db_path=db)
    assert _soma(db) == (2, 107.0)


def test_meta_migra_chave_antiga(tmp_path):
    from meta_ads.loader import salvar, _CREATE_TABLE_TPL
    db = tmp_path / "m.db"
    antigo = (_CREATE_TABLE_TPL.format(tabela="t")
              .replace("ad_id               TEXT NOT NULL,", "ad_id               TEXT,")
              .replace("account_id          TEXT NOT NULL DEFAULT '',", "account_id          TEXT,"))
    antigo = antigo[:antigo.index("-- Grão")] + "UNIQUE(data, campanha, ad_id)\n)"
    with sqlite3.connect(db) as c:
        c.execute(antigo)
        c.executemany("INSERT INTO t (data, campanha, ad_id, account_id, investimento) VALUES (?,?,?,?,?)", [
            ("2026-10-01", "Velho nome", "a1", "act_1", 50.0),   # mesmo anúncio, nome antigo
            ("2026-10-01", "Novo nome", "a1", "1", 50.0),         # ... renomeado: duplicata
            ("2026-10-01", "X", None, None, 9.0),                 # sem ad_id
        ])
    salvar([_linha()], append=True, tabela="t", db_path=db)
    assert _soma(db) == (2, 150.0)


# ── Transcrição e Notion: falha fechada ───────────────────────────────────────

@pytest.fixture
def transcricao(monkeypatch, tmp_path):
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "tz.py").write_text("from datetime import datetime, timezone, date\n"
                                "def hoje_brt(): return date.today()\n"
                                "def agora_utc_iso(): return datetime.now(timezone.utc).isoformat()\n")
    for p in (str(shim), str(RAIZ / "transcricao"), str(RAIZ)):
        monkeypatch.syspath_prepend(p)
    sys.modules.pop("tz", None)
    pytest.importorskip("googleapiclient")
    import sync_reunioes, criar_tarefas_notion
    return sync_reunioes, criar_tarefas_notion


def test_mongo_fora_pula_em_vez_de_reprocessar(transcricao, monkeypatch):
    sync_reunioes, _ = transcricao
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE reunioes (id INTEGER PRIMARY KEY, fonte_id TEXT)")
    monkeypatch.delenv("TRANSCRICAO_SEM_MONGO", raising=False)
    monkeypatch.setattr(sync_reunioes, "get_mongo_col", lambda: None)
    assert sync_reunioes.ja_processado(conn, "arquivo-novo") is True

    class Col:
        def find_one(self, q, p): return {"_id": 1} if q["fonte_id"] == "antigo" else None
    monkeypatch.setattr(sync_reunioes, "get_mongo_col", lambda: Col())
    assert sync_reunioes.ja_processado(conn, "antigo") and not sync_reunioes.ja_processado(conn, "novo")


def test_dedup_notion_pagina_e_falha_fechada(transcricao, monkeypatch):
    _, ctn = transcricao
    paginas = [{"results": [{"properties": {"Tarefa": {"title": [{"plain_text": f"T{i}"}]}}} for i in range(100)],
                "has_more": True, "next_cursor": "c2"},
               {"results": [{"properties": {"Tarefa": {"title": [{"plain_text": "T-pagina-2"}]}}}], "has_more": False}]

    class R:
        def __init__(self, j, st=200): self._j, self.status_code, self.text = j, st, ""
        def json(self): return self._j
    monkeypatch.setattr(ctn.requests, "post", lambda *a, **k: R(paginas.pop(0)))
    titulos = ctn.cards_ja_criados_notion("f")
    assert len(titulos) == 101 and "T-pagina-2" in titulos

    monkeypatch.setattr(ctn.requests, "post", lambda *a, **k: R({}, 502))
    with pytest.raises(RuntimeError):
        ctn.cards_ja_criados_notion("f")
