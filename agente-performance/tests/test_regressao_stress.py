"""
tests/test_regressao_stress.py

Teste de stress de não-regressão do is_bot_suspect.

Simula o ciclo real que causou o bug:
  - Carga 1: detecta 08/10 como bot, grava is_bot_suspect=1 no SQLite
  - Carga 2..N: recargas do cron (a cada 15 min) — _detectar_bots não detecta mais
    (janela mudou / mediana alta) e envia is_bot_suspect=0
  - Após cada recarga, verifica que o flag permanece 1 no SQLite E no payload BQ

Adicionalmente:
  - Verifica N=50 recargas consecutivas (stress)
  - Verifica que dois clientes em paralelo não se contaminam
  - Verifica que o flag de um dia NOVO detectado após o bot nunca é afetado
  - Verifica o estado do banco real da SO (2026-10-08 deve estar com 1)
"""

import logging
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.sync_shopify_sessions import _DDL, salvar_sessoes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _criar_db(tmp_path: Path, tabela: str) -> Path:
    db = tmp_path / f"{tabela}.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_DDL.format(tabela=tabela))
    conn.commit()
    conn.close()
    return db


def _gravar(db: Path, tabela: str, data: str, sessoes: int, is_bot: int):
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_DDL.format(tabela=tabela))
    conn.execute(
        f"INSERT OR REPLACE INTO {tabela} "
        "(data, sessoes, sessoes_com_carrinho, sessoes_checkout, "
        "sessoes_compra, taxa_conversao, is_bot_suspect) "
        "VALUES (?,?,0,0,0,0.05,?)",
        (data, sessoes, is_bot),
    )
    conn.commit()
    conn.close()


def _ler(db: Path, tabela: str, data: str) -> int:
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        f"SELECT is_bot_suspect FROM {tabela} WHERE data=?", (data,)
    ).fetchone()
    conn.close()
    return row[0] if row else -1


def _row(data: str, sessoes: int, is_bot: int) -> dict:
    return {
        "data": data,
        "sessoes": sessoes,
        "sessoes_com_carrinho": 0,
        "sessoes_checkout": 0,
        "sessoes_compra": 0,
        "taxa_conversao": 0.05,
        "is_bot_suspect": is_bot,
    }


def _capturar_bq_row(row_pedido: dict, cliente: str = "source_of") -> dict | None:
    """Retorna o bq_row montado por write_shopify para um único row."""
    captured = {}

    def fake_insert(table_id, bq_rows, desc):
        if bq_rows:
            captured["row"] = bq_rows[0]
        return len(bq_rows)

    with patch("db.bq_writer.enabled", return_value=True), \
         patch("db.bq_writer._insert", side_effect=fake_insert), \
         patch.dict(os.environ, {"BQ_PROJECT": "p", "BQ_DATASET": "d"}):
        from db.bq_writer import write_shopify
        write_shopify([row_pedido], cliente=cliente)

    return captured.get("row")


def _capturar_sql_sessoes(rows: list, cliente: str = "source_of") -> str:
    """Retorna o SQL MERGE gerado por write_shopify_sessoes."""
    captured = {}
    mock_bq = MagicMock()
    mock_bq.project = "p"

    def fake_query(sql):
        captured["sql"] = sql
        r = MagicMock()
        r.result.return_value = None
        return r

    mock_bq.query.side_effect = fake_query

    with patch("db.bq_writer.enabled", return_value=True), \
         patch("db.bq_writer._client", return_value=mock_bq), \
         patch.dict(os.environ, {"BQ_PROJECT": "p", "BQ_DATASET": "d"}):
        from db.bq_writer import write_shopify_sessoes
        write_shopify_sessoes(rows, cliente=cliente)

    return captured.get("sql", "")


# ---------------------------------------------------------------------------
# Stress: 50 recargas consecutivas
# ---------------------------------------------------------------------------

class TestStressRecargas:

    def test_50_recargas_flag_permanece_1(self, tmp_path):
        """
        Simula 50 execuções do cron_shopify_sync enviando is_bot_suspect=0.
        O flag deve permanecer 1 em todas as iterações.
        """
        tabela = "source_of_shopify_sessoes"
        dia_bot = "2026-10-08"

        # Carga inicial: bot detectado
        db = _criar_db(tmp_path, tabela)
        _gravar(db, tabela, dia_bot, 8923, 1)
        assert _ler(db, tabela, dia_bot) == 1

        # 50 recargas com is_bot_suspect=0
        for i in range(50):
            rows = [_row(dia_bot, 8923, 0)]  # cron envia 0
            salvar_sessoes(rows, tabela=tabela, db_path=db)
            flag = _ler(db, tabela, dia_bot)
            assert flag == 1, \
                f"Recarga {i+1}/50: flag regrediu para {flag} — FALHA"

    def test_50_recargas_com_janela_de_dias(self, tmp_path):
        """
        Stress com janela de 30 dias: 08/10 é bot, os outros são normais.
        50 recargas regravando a janela inteira com is_bot_suspect=0 exceto 08/10.
        """
        tabela = "source_of_shopify_sessoes"
        dia_bot = "2026-10-08"
        inicio = date(2026, 9, 18)

        db = _criar_db(tmp_path, tabela)

        # Estado inicial: 21 dias, 08/10 com bot=1
        rows_init = []
        for i in range(21):
            d = str(inicio + timedelta(days=i))
            rows_init.append(_row(d, 8923 if d == dia_bot else 1500, 1 if d == dia_bot else 0))
        salvar_sessoes(rows_init, tabela=tabela, db_path=db)
        assert _ler(db, tabela, dia_bot) == 1

        # 50 recargas: todos os dias com is_bot_suspect=0
        for i in range(50):
            rows_recarga = [
                _row(str(inicio + timedelta(days=j)), 8923 if str(inicio + timedelta(days=j)) == dia_bot else 1500, 0)
                for j in range(21)
            ]
            salvar_sessoes(rows_recarga, tabela=tabela, db_path=db)
            flag = _ler(db, tabela, dia_bot)
            assert flag == 1, f"Iteração {i+1}: flag do bot regrediu para {flag}"

    def test_50_recargas_outros_dias_nao_afetados(self, tmp_path):
        """
        Durante 50 recargas, os dias normais (nunca flagados) devem permanecer 0.
        """
        tabela = "source_of_shopify_sessoes"
        dia_bot = "2026-10-08"
        dia_normal = "2026-10-07"

        db = _criar_db(tmp_path, tabela)
        _gravar(db, tabela, dia_bot,    8923, 1)
        _gravar(db, tabela, dia_normal, 1500, 0)

        for i in range(50):
            rows = [
                _row(dia_bot,    8923, 0),
                _row(dia_normal, 1500, 0),
            ]
            salvar_sessoes(rows, tabela=tabela, db_path=db)
            assert _ler(db, tabela, dia_bot)    == 1, f"iter {i+1}: bot regrediu"
            assert _ler(db, tabela, dia_normal) == 0, f"iter {i+1}: normal virou 1"


# ---------------------------------------------------------------------------
# Dois clientes em paralelo
# ---------------------------------------------------------------------------

class TestDoisClientesParalelo:

    def test_so_e_pod_independentes_50_recargas(self, tmp_path):
        """
        SO: 08/10 é bot. POD: mesma data, sessões normais.
        50 recargas de cada — flags não devem se contaminar.
        """
        tab_so  = "source_of_shopify_sessoes"
        tab_pod = "pod_kombucha_shopify_sessoes"
        dia = "2026-10-08"

        db_so  = _criar_db(tmp_path, tab_so)
        db_pod = _criar_db(tmp_path, tab_pod)

        _gravar(db_so,  tab_so,  dia, 8923, 1)
        _gravar(db_pod, tab_pod, dia, 1200, 0)

        for i in range(50):
            salvar_sessoes([_row(dia, 8923, 0)], tabela=tab_so,  db_path=db_so)
            salvar_sessoes([_row(dia, 1200, 0)], tabela=tab_pod, db_path=db_pod)

            assert _ler(db_so,  tab_so,  dia) == 1, f"iter {i+1}: SO bot regrediu"
            assert _ler(db_pod, tab_pod, dia) == 0, f"iter {i+1}: POD contaminado"


# ---------------------------------------------------------------------------
# Log de restauração em todas as recargas
# ---------------------------------------------------------------------------

class TestLogRestauracao:

    def test_warning_emitido_em_cada_recarga(self, tmp_path, caplog):
        """
        Cada recarga que restaura o flag deve emitir exatamente 1 WARNING.
        """
        tabela = "source_of_shopify_sessoes"
        dia_bot = "2026-10-08"

        db = _criar_db(tmp_path, tabela)
        _gravar(db, tabela, dia_bot, 8923, 1)

        for i in range(10):
            with caplog.at_level(logging.WARNING, logger="scripts.sync_shopify_sessions"):
                caplog.clear()
                salvar_sessoes([_row(dia_bot, 8923, 0)], tabela=tabela, db_path=db)
                msgs = [r.message for r in caplog.records if "restaurado" in r.message]
                assert len(msgs) == 1, \
                    f"Recarga {i+1}: esperado 1 WARNING de restauração, obtido {len(msgs)}"
                assert dia_bot in msgs[0]

    def test_sem_warning_quando_flag_ja_e_1(self, tmp_path, caplog):
        """
        Se o row já vem com is_bot_suspect=1 (detecção funcionou), não deve emitir WARNING.
        """
        tabela = "source_of_shopify_sessoes"
        dia_bot = "2026-10-08"

        db = _criar_db(tmp_path, tabela)
        _gravar(db, tabela, dia_bot, 8923, 1)

        with caplog.at_level(logging.WARNING, logger="scripts.sync_shopify_sessions"):
            # Row já vem com 1 — sem necessidade de restauração
            salvar_sessoes([_row(dia_bot, 8923, 1)], tabela=tabela, db_path=db)
            msgs = [r.message for r in caplog.records if "restaurado" in r.message]
            assert len(msgs) == 0, "Não deve emitir WARNING quando row já tem is_bot_suspect=1"


# ---------------------------------------------------------------------------
# BQ Camada 2: stress com write_shopify
# ---------------------------------------------------------------------------

class TestBQCamada2Stress:

    def test_20_chamadas_write_shopify_sem_campo_sempre_none(self):
        """
        20 chamadas de write_shopify sem is_bot_suspect no row.
        Todas devem gerar None no payload BQ — nunca 0.
        """
        for i in range(20):
            row = {
                "data": f"2026-10-{(i % 19) + 1:02d}",
                "receita": 5000.0, "frete": 0.0, "pedidos": 50,
                "novos_clientes": 20, "recompradores": 30, "ticket_medio": 100.0,
                "total_desconto": 0.0, "pedidos_acima_300": 0, "receita_criada": 5000.0,
                "pedidos_criados": 50, "pedidos_meta": 30, "receita_meta": 3000.0,
                "novos_meta": 10, "pedidos_google": 10, "receita_google": 1000.0,
                "novos_google": 5, "pedidos_outros": 10, "receita_outros": 1000.0,
                "novos_outros": 5, "gross_sales": 5200.0, "pedidos_gt300": 0,
                "sessoes": None, "sessoes_com_carrinho": None,
                "sessoes_checkout": None, "sessoes_compra": None, "taxa_conversao": None,
                # is_bot_suspect AUSENTE
            }
            bq_row = _capturar_bq_row(row)
            assert bq_row is not None
            assert bq_row["is_bot_suspect"] is None, \
                f"Chamada {i+1}: esperado None, obtido {bq_row['is_bot_suspect']!r}"


# ---------------------------------------------------------------------------
# BQ Camada 3: stress com write_shopify_sessoes
# ---------------------------------------------------------------------------

class TestBQCamada3Stress:

    def test_20_chamadas_sql_sempre_tem_greatest(self):
        """
        20 chamadas de write_shopify_sessoes com is_bot_suspect=0.
        O SQL gerado deve sempre conter GREATEST — sem exceção.
        """
        import re
        for i in range(20):
            rows = [
                {
                    "data": f"2026-10-{(i % 19) + 1:02d}",
                    "sessoes": 8923 if i == 0 else 1500,
                    "sessoes_com_carrinho": 0,
                    "sessoes_checkout": 0,
                    "sessoes_compra": 0,
                    "taxa_conversao": 0.009 if i == 0 else 0.05,
                    "is_bot_suspect": 0,  # cron envia 0 após recargas
                }
            ]
            sql = _capturar_sql_sessoes(rows)
            assert "GREATEST" in sql, \
                f"Chamada {i+1}: GREATEST ausente no SQL gerado"
            assert "T.is_bot_suspect       = S.is_bot_suspect" not in sql, \
                f"Chamada {i+1}: sobrescrita direta encontrada no SQL"

    def test_greatest_matematica_todos_casos(self):
        """Verifica os 7 casos matemáticos do GREATEST(COALESCE(T,0), COALESCE(S,0))."""
        casos = [
            (1, 0, 1),   # não regride
            (1, 1, 1),   # mantém
            (0, 0, 0),   # normal
            (0, 1, 1),   # novo flag
            (None, 1, 1),
            (1, None, 1),
            (None, None, 0),
        ]
        def g(t, s):
            return max(0 if t is None else t, 0 if s is None else s)

        for t, s, esperado in casos:
            assert g(t, s) == esperado, \
                f"GREATEST(COALESCE({t},0), COALESCE({s},0)) = {g(t,s)}, esperado {esperado}"


# ---------------------------------------------------------------------------
# Verifica o banco REAL de produção
# ---------------------------------------------------------------------------

class TestBancoReal:

    def test_08out_so_banco_real_is_bot_suspect_1(self):
        """
        Verifica o estado atual do banco SQLite de produção da Source OF.
        2026-10-08 deve ter is_bot_suspect=1 (8.923 sessões, taxa 0,997%).
        Este teste FALHA se o cron regressar o flag.
        """
        db_path = Path(__file__).parent.parent / "data/performance/source_of/shopify.db"
        if not db_path.exists():
            pytest.skip(f"Banco de produção não encontrado: {db_path}")

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT sessoes, taxa_conversao, is_bot_suspect "
            "FROM source_of_shopify_sessoes WHERE data = '2026-10-08'"
        ).fetchone()
        conn.close()

        assert row is not None, "Dia 2026-10-08 não encontrado no banco de produção"
        sessoes, taxa, flag = row
        assert flag == 1, \
            f"REGRESSÃO DETECTADA NO BANCO REAL: 2026-10-08 is_bot_suspect={flag} " \
            f"(sessoes={sessoes}, taxa={taxa:.3%})"
        assert sessoes == 8923, f"Sessões inesperadas: {sessoes} (esperado 8923)"

    def test_08out_so_unico_dia_bot_no_periodo(self):
        """
        Na janela set/out 2026, apenas 08/10 deve ter is_bot_suspect=1.
        Garante que não houve falsos positivos introduzidos pelas recargas.
        """
        db_path = Path(__file__).parent.parent / "data/performance/source_of/shopify.db"
        if not db_path.exists():
            pytest.skip("Banco de produção não encontrado")

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT data, sessoes, is_bot_suspect "
            "FROM source_of_shopify_sessoes "
            "WHERE data BETWEEN '2026-09-01' AND '2026-10-19' "
            "ORDER BY data"
        ).fetchall()
        conn.close()

        bots = [(d, s) for d, s, f in rows if f == 1]
        assert len(bots) == 1, \
            f"Esperado exatamente 1 dia bot em set-out, encontrado {len(bots)}: {bots}"
        assert bots[0][0] == "2026-10-08", \
            f"Dia bot inesperado: {bots[0][0]} (esperado 2026-10-08)"
