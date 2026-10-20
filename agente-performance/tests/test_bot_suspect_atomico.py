"""
tests/test_bot_suspect_atomico.py

Testa a preservação atômica de is_bot_suspect=1 dentro de salvar_sessoes().

Cobre:
  1. Regressão bloqueada: flag=1 no SQLite não volta para 0 numa recarga
  2. Log WARNING emitido quando restauração acontece
  3. Flag novo detectado normalmente (não existe no SQLite ainda)
  4. Dois clientes independentes (tabelas diferentes, sem contaminação)
  5. Cenário real: 08/10 SO com 8.923 sessões preserva flag entre recargas
  6. Atomicidade: rows sem pico mas com flag no DB ficam com 1 após save
"""

import logging
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.sync_shopify_sessions import _DDL, salvar_sessoes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path, tabela: str, rows_iniciais: list[dict]) -> Path:
    """Cria um SQLite temporário e insere linhas iniciais."""
    db = tmp_path / f"{tabela}.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_DDL.format(tabela=tabela))
    for r in rows_iniciais:
        conn.execute(
            f"INSERT OR REPLACE INTO {tabela} "
            "(data, sessoes, sessoes_com_carrinho, sessoes_checkout, "
            "sessoes_compra, taxa_conversao, is_bot_suspect) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                r["data"], r["sessoes"],
                r.get("sessoes_com_carrinho", 0),
                r.get("sessoes_checkout", 0),
                r.get("sessoes_compra", 0),
                r.get("taxa_conversao", 0.05),
                r.get("is_bot_suspect", 0),
            ],
        )
    conn.commit()
    conn.close()
    return db


def _ler_flag(db: Path, tabela: str, data: str) -> int:
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        f"SELECT is_bot_suspect FROM {tabela} WHERE data = ?", (data,)
    ).fetchone()
    conn.close()
    return row[0] if row else -1


def _dias(n: int, base: str = "2026-09-01", sessoes: int = 1500) -> list[dict]:
    inicio = date.fromisoformat(base)
    return [
        {
            "data": str(inicio + timedelta(days=i)),
            "sessoes": sessoes,
            "sessoes_com_carrinho": 0,
            "sessoes_checkout": 0,
            "sessoes_compra": 0,
            "taxa_conversao": 0.05,
            "is_bot_suspect": 0,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

class TestSalvarSessoesPreservacaoAtomica:

    def test_flag_1_no_db_nao_regride_para_0(self, tmp_path):
        """Cenário principal: recarga com is_bot_suspect=0 não sobrescreve 1 existente."""
        tabela = "so_sessoes"
        dia_bot = "2026-10-08"

        # Estado inicial: dia do bot já gravado como 1
        inicial = [{"data": dia_bot, "sessoes": 8923, "sessoes_com_carrinho": 0,
                    "sessoes_checkout": 0, "sessoes_compra": 0,
                    "taxa_conversao": 0.009, "is_bot_suspect": 1}]
        db = _make_db(tmp_path, tabela, inicial)

        assert _ler_flag(db, tabela, dia_bot) == 1

        # Recarga com is_bot_suspect=0 (como vem do _detectar_bots sem histórico)
        recarga = [{"data": dia_bot, "sessoes": 8923, "sessoes_com_carrinho": 0,
                    "sessoes_checkout": 0, "sessoes_compra": 0,
                    "taxa_conversao": 0.009, "is_bot_suspect": 0}]
        salvar_sessoes(recarga, tabela=tabela, db_path=db)

        assert _ler_flag(db, tabela, dia_bot) == 1, \
            "is_bot_suspect=1 não deve regredir para 0 numa recarga"

    def test_flag_novo_detectado_e_salvo(self, tmp_path):
        """Dia novo com is_bot_suspect=1 é salvo normalmente."""
        tabela = "so_sessoes"
        dia_bot = "2026-10-08"

        # DB vazio (sem registro anterior)
        db = _make_db(tmp_path, tabela, [])

        rows = [{"data": dia_bot, "sessoes": 8923, "sessoes_com_carrinho": 0,
                 "sessoes_checkout": 0, "sessoes_compra": 0,
                 "taxa_conversao": 0.009, "is_bot_suspect": 1}]
        salvar_sessoes(rows, tabela=tabela, db_path=db)

        assert _ler_flag(db, tabela, dia_bot) == 1

    def test_dia_normal_sem_flag_permanece_0(self, tmp_path):
        """Dia normal (nunca flagado) permanece is_bot_suspect=0."""
        tabela = "so_sessoes"
        dia = "2026-10-07"

        inicial = [{"data": dia, "sessoes": 1500, "sessoes_com_carrinho": 0,
                    "sessoes_checkout": 0, "sessoes_compra": 0,
                    "taxa_conversao": 0.05, "is_bot_suspect": 0}]
        db = _make_db(tmp_path, tabela, inicial)

        recarga = [{"data": dia, "sessoes": 1600, "sessoes_com_carrinho": 0,
                    "sessoes_checkout": 0, "sessoes_compra": 0,
                    "taxa_conversao": 0.05, "is_bot_suspect": 0}]
        salvar_sessoes(recarga, tabela=tabela, db_path=db)

        assert _ler_flag(db, tabela, dia) == 0

    def test_log_warning_emitido_na_restauracao(self, tmp_path, caplog):
        """WARNING deve ser emitido quando o flag é restaurado."""
        tabela = "so_sessoes"
        dia_bot = "2026-10-08"

        inicial = [{"data": dia_bot, "sessoes": 8923, "sessoes_com_carrinho": 0,
                    "sessoes_checkout": 0, "sessoes_compra": 0,
                    "taxa_conversao": 0.009, "is_bot_suspect": 1}]
        db = _make_db(tmp_path, tabela, inicial)

        recarga = [{"data": dia_bot, "sessoes": 8923, "sessoes_com_carrinho": 0,
                    "sessoes_checkout": 0, "sessoes_compra": 0,
                    "taxa_conversao": 0.009, "is_bot_suspect": 0}]

        with caplog.at_level(logging.WARNING, logger="scripts.sync_shopify_sessions"):
            salvar_sessoes(recarga, tabela=tabela, db_path=db)

        msgs = [r.message for r in caplog.records if "restaurado" in r.message]
        assert msgs, f"WARNING de restauração não emitido. Logs: {caplog.records}"
        assert dia_bot in msgs[0]
        assert "regressão bloqueada" in msgs[0]

    def test_sem_restauracao_sem_warning(self, tmp_path, caplog):
        """Sem regressão → sem WARNING de restauração."""
        tabela = "so_sessoes"
        dia = "2026-10-07"

        # DB com flag=0, recarga com flag=0 → nada a restaurar
        inicial = [{"data": dia, "sessoes": 1500, "sessoes_com_carrinho": 0,
                    "sessoes_checkout": 0, "sessoes_compra": 0,
                    "taxa_conversao": 0.05, "is_bot_suspect": 0}]
        db = _make_db(tmp_path, tabela, inicial)

        recarga = [{"data": dia, "sessoes": 1500, "sessoes_com_carrinho": 0,
                    "sessoes_checkout": 0, "sessoes_compra": 0,
                    "taxa_conversao": 0.05, "is_bot_suspect": 0}]

        with caplog.at_level(logging.WARNING, logger="scripts.sync_shopify_sessions"):
            salvar_sessoes(recarga, tabela=tabela, db_path=db)

        msgs_restauracao = [r.message for r in caplog.records if "restaurado" in r.message]
        assert not msgs_restauracao, "Não deve emitir WARNING sem regressão"

    def test_multiplos_dias_preservacao_seletiva(self, tmp_path):
        """Apenas os dias flagados no DB são preservados — os outros ficam com o valor novo."""
        tabela = "so_sessoes"
        dia_bot = "2026-10-08"
        dia_normal = "2026-10-07"

        inicial = [
            {"data": dia_bot,    "sessoes": 8923, "sessoes_com_carrinho": 0,
             "sessoes_checkout": 0, "sessoes_compra": 0, "taxa_conversao": 0.009, "is_bot_suspect": 1},
            {"data": dia_normal, "sessoes": 1500, "sessoes_com_carrinho": 0,
             "sessoes_checkout": 0, "sessoes_compra": 0, "taxa_conversao": 0.05,  "is_bot_suspect": 0},
        ]
        db = _make_db(tmp_path, tabela, inicial)

        # Recarga: ambos com is_bot_suspect=0
        recarga = [
            {"data": dia_bot,    "sessoes": 8923, "sessoes_com_carrinho": 0,
             "sessoes_checkout": 0, "sessoes_compra": 0, "taxa_conversao": 0.009, "is_bot_suspect": 0},
            {"data": dia_normal, "sessoes": 1600, "sessoes_com_carrinho": 0,
             "sessoes_checkout": 0, "sessoes_compra": 0, "taxa_conversao": 0.05,  "is_bot_suspect": 0},
        ]
        salvar_sessoes(recarga, tabela=tabela, db_path=db)

        assert _ler_flag(db, tabela, dia_bot)    == 1, "dia_bot deve ser preservado"
        assert _ler_flag(db, tabela, dia_normal) == 0, "dia_normal não deve ser afetado"

    def test_duas_tabelas_independentes(self, tmp_path):
        """Flags de tabelas diferentes não se contaminam."""
        tab_so  = "source_of_shopify_sessoes"
        tab_pod = "pod_kombucha_shopify_sessoes"
        dia = "2026-10-08"

        db_so  = _make_db(tmp_path, tab_so,  [{"data": dia, "sessoes": 8923, "sessoes_com_carrinho": 0,
                                                "sessoes_checkout": 0, "sessoes_compra": 0,
                                                "taxa_conversao": 0.009, "is_bot_suspect": 1}])
        db_pod = _make_db(tmp_path, tab_pod, [{"data": dia, "sessoes": 1200, "sessoes_com_carrinho": 0,
                                                "sessoes_checkout": 0, "sessoes_compra": 0,
                                                "taxa_conversao": 0.05,  "is_bot_suspect": 0}])

        # Recarga SO com 0 → deve restaurar para 1
        salvar_sessoes(
            [{"data": dia, "sessoes": 8923, "sessoes_com_carrinho": 0, "sessoes_checkout": 0,
              "sessoes_compra": 0, "taxa_conversao": 0.009, "is_bot_suspect": 0}],
            tabela=tab_so, db_path=db_so,
        )
        # Recarga POD com 0 → deve permanecer 0
        salvar_sessoes(
            [{"data": dia, "sessoes": 1200, "sessoes_com_carrinho": 0, "sessoes_checkout": 0,
              "sessoes_compra": 0, "taxa_conversao": 0.05,  "is_bot_suspect": 0}],
            tabela=tab_pod, db_path=db_pod,
        )

        assert _ler_flag(db_so,  tab_so,  dia) == 1, "SO deve ter flag preservado"
        assert _ler_flag(db_pod, tab_pod, dia) == 0, "POD não deve ter flag contaminado"

    def test_db_vazio_sem_erro(self, tmp_path):
        """salvar_sessoes em DB vazio não deve lançar exceção."""
        tabela = "so_sessoes"
        db = tmp_path / "vazio.db"  # não existe ainda

        rows = _dias(5)
        salvar_sessoes(rows, tabela=tabela, db_path=db)

        # Deve ter criado a tabela e inserido os rows
        conn = sqlite3.connect(str(db))
        count = conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
        conn.close()
        assert count == 5

    def test_rows_vazio_retorna_zero(self, tmp_path):
        """Lista vazia retorna 0 sem tocar o DB."""
        tabela = "so_sessoes"
        db = tmp_path / "noop.db"
        result = salvar_sessoes([], tabela=tabela, db_path=db)
        assert result == 0
        assert not db.exists(), "DB não deve ser criado para lista vazia"

    def test_cenario_real_08out_preservado_entre_recargas(self, tmp_path):
        """
        Replica o cenário reportado em 2026-10-20:
        Carga 1 detecta 08/10 como bot (8923 sessões).
        Carga 2 recalcula sem contexto histórico suficiente → is_bot_suspect=0.
        salvar_sessoes() deve restaurar para 1.
        """
        tabela = "source_of_shopify_sessoes"
        dia_bot = "2026-10-08"

        # Carga 1: detecção correta
        carga1 = [{"data": dia_bot, "sessoes": 8923, "sessoes_com_carrinho": 0,
                   "sessoes_checkout": 0, "sessoes_compra": 89,
                   "taxa_conversao": 0.009974, "is_bot_suspect": 1}]
        db = _make_db(tmp_path, tabela, carga1)
        assert _ler_flag(db, tabela, dia_bot) == 1

        # Carga 2: _detectar_bots falha em detectar (janela insuficiente ou dados mudaram)
        carga2 = [{"data": dia_bot, "sessoes": 8923, "sessoes_com_carrinho": 0,
                   "sessoes_checkout": 0, "sessoes_compra": 89,
                   "taxa_conversao": 0.009974, "is_bot_suspect": 0}]
        salvar_sessoes(carga2, tabela=tabela, db_path=db)

        assert _ler_flag(db, tabela, dia_bot) == 1, \
            "Cenário real 08/10: flag deve ser preservado entre recargas"
