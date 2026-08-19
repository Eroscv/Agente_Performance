"""
tests/test_duck_connection.py

Etapa 1 — Teste de paridade DuckDB vs SQLite federado.

Valida que:
  1. DuckDB conecta e carrega as views sem erro
  2. Contagens de linhas são idênticas (SQLite vs DuckDB)
  3. Somas de métricas-chave batem com tolerância de 0.01%
  4. Window functions DuckDB funcionam (só disponíveis no DuckDB)
  5. reset_duck_conn() recria conexão corretamente
  6. query_duck() retorna list[dict] com mesma estrutura de query_fed()
"""

import pytest
from db.duck import get_duck_conn, query_duck, query_duck_one, reset_duck_conn, inspect_duck
from db.connections import query_fed, reset_federated_conn


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def duck():
    """Conexão DuckDB compartilhada por todos os testes do módulo."""
    reset_duck_conn()
    conn = get_duck_conn()
    yield conn
    reset_duck_conn()


@pytest.fixture(scope="module")
def sqlite_fed():
    """Garante conexão SQLite federada limpa."""
    reset_federated_conn()
    yield
    reset_federated_conn()


# ── 1. Conexão e views ────────────────────────────────────────────────────────

class TestDuckConnection:

    def test_connect_sem_erro(self, duck):
        """DuckDB conecta e responde."""
        result = duck.execute("SELECT 42 AS answer").fetchone()
        assert result[0] == 42

    def test_versao_duckdb(self, duck):
        """DuckDB versão >= 1.0."""
        import duckdb
        major = int(duckdb.__version__.split(".")[0])
        assert major >= 1, f"DuckDB muito antigo: {duckdb.__version__}"

    def test_view_campanhas_unificada_existe(self, duck):
        """View campanhas_unificada foi criada."""
        result = duck.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'campanhas_unificada'"
        ).fetchone()
        assert result is not None, "View campanhas_unificada não encontrada"

    def test_view_v_performance_existe(self, duck):
        """View v_performance foi criada."""
        result = duck.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'v_performance'"
        ).fetchone()
        assert result is not None, "View v_performance não encontrada"

    def test_view_shopify_existe(self, duck):
        """View shopify_receita_diaria foi criada."""
        result = duck.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'shopify_receita_diaria'"
        ).fetchone()
        assert result is not None, "View shopify_receita_diaria não encontrada"

    def test_dbs_attachados(self):
        """Pelo menos os DBs principais foram attachados."""
        info = inspect_duck()
        assert len(info["attached_dbs"]) >= 2, (
            f"Poucos DBs attachados: {info['attached_dbs']}"
        )

    def test_inspect_retorna_row_counts(self):
        """inspect_duck() retorna contagens válidas."""
        info = inspect_duck()
        assert "row_counts" in info
        assert "campanhas_unificada" in info["row_counts"]
        assert info["row_counts"]["campanhas_unificada"] > 0


# ── 2. Paridade de contagens ──────────────────────────────────────────────────

class TestParidadeContagens:

    def test_contagem_campanhas_unificada(self, sqlite_fed):
        """Contagem total deve ser idêntica entre SQLite e DuckDB."""
        sql = "SELECT COUNT(*) AS n FROM campanhas_unificada"

        rows_sq = query_fed(sql)
        rows_dk = query_duck(sql)

        n_sqlite = rows_sq[0]["n"]
        n_duck   = rows_dk[0]["n"]

        assert n_sqlite == n_duck, (
            f"Contagem diverge: SQLite={n_sqlite}, DuckDB={n_duck}"
        )

    def test_contagem_por_cliente(self, sqlite_fed):
        """Contagem por cliente_key deve ser idêntica."""
        sql = """
            SELECT cliente_key, COUNT(*) AS n
            FROM campanhas_unificada
            GROUP BY cliente_key
            ORDER BY cliente_key
        """
        rows_sq = {r["cliente_key"]: r["n"] for r in query_fed(sql)}
        rows_dk = {r["cliente_key"]: r["n"] for r in query_duck(sql)}

        assert rows_sq == rows_dk, (
            f"Contagem por cliente diverge:\n  SQLite: {rows_sq}\n  DuckDB: {rows_dk}"
        )

    def test_contagem_por_plataforma(self, sqlite_fed):
        """Contagem por plataforma deve ser idêntica."""
        sql = """
            SELECT plataforma, COUNT(*) AS n
            FROM campanhas_unificada
            GROUP BY plataforma
            ORDER BY plataforma
        """
        rows_sq = {r["plataforma"]: r["n"] for r in query_fed(sql)}
        rows_dk = {r["plataforma"]: r["n"] for r in query_duck(sql)}

        assert rows_sq == rows_dk, (
            f"Contagem por plataforma diverge:\n  SQLite: {rows_sq}\n  DuckDB: {rows_dk}"
        )

    def test_contagem_shopify(self, sqlite_fed):
        """Contagem Shopify deve ser idêntica."""
        sql = "SELECT COUNT(*) AS n FROM shopify_receita_diaria"

        n_sqlite = query_fed(sql)[0]["n"]
        n_duck   = query_duck(sql)[0]["n"]

        assert n_sqlite == n_duck, (
            f"Shopify diverge: SQLite={n_sqlite}, DuckDB={n_duck}"
        )


# ── 3. Paridade de métricas ───────────────────────────────────────────────────

class TestParidadeMetricas:

    def _tolerancia(self, a: float, b: float, tol: float = 0.0001) -> bool:
        """Verifica se dois valores estão dentro da tolerância relativa (0.01%)."""
        if a == 0 and b == 0:
            return True
        if a == 0 or b == 0:
            return abs(a - b) < 0.01
        return abs(a - b) / max(abs(a), abs(b)) <= tol

    def test_soma_investimento_source_of(self, sqlite_fed):
        """Soma de investimento Source OF deve bater com tolerância de 0.01%."""
        sql = """
            SELECT SUM(investimento) AS total
            FROM campanhas_unificada
            WHERE cliente_key = 'source_of'
        """
        v_sq = query_fed(sql)[0]["total"] or 0
        v_dk = query_duck(sql)[0]["total"] or 0

        assert self._tolerancia(v_sq, v_dk), (
            f"Investimento Source OF diverge: SQLite={v_sq:.2f}, DuckDB={v_dk:.2f}"
        )

    def test_soma_investimento_pod_kombucha(self, sqlite_fed):
        """Soma de investimento POD Kombucha deve bater."""
        sql = """
            SELECT SUM(investimento) AS total
            FROM campanhas_unificada
            WHERE cliente_key = 'pod_kombucha'
        """
        v_sq = query_fed(sql)[0]["total"] or 0
        v_dk = query_duck(sql)[0]["total"] or 0

        assert self._tolerancia(v_sq, v_dk), (
            f"Investimento POD diverge: SQLite={v_sq:.2f}, DuckDB={v_dk:.2f}"
        )

    def test_soma_valor_compras(self, sqlite_fed):
        """Soma de valor_compras (receita) deve bater."""
        sql = "SELECT SUM(valor_compras) AS total FROM campanhas_unificada"

        v_sq = query_fed(sql)[0]["total"] or 0
        v_dk = query_duck(sql)[0]["total"] or 0

        assert self._tolerancia(v_sq, v_dk), (
            f"valor_compras diverge: SQLite={v_sq:.2f}, DuckDB={v_dk:.2f}"
        )

    def test_roas_medio_source_of_meta(self, sqlite_fed):
        """ROAS médio Source OF Meta Ads deve bater."""
        sql = """
            SELECT ROUND(AVG(roas_compras), 4) AS roas_medio
            FROM campanhas_unificada
            WHERE cliente_key = 'source_of' AND plataforma = 'meta_ads'
              AND roas_compras IS NOT NULL
        """
        v_sq = query_fed(sql)[0]["roas_medio"] or 0
        v_dk = query_duck(sql)[0]["roas_medio"] or 0

        assert self._tolerancia(v_sq, v_dk), (
            f"ROAS médio diverge: SQLite={v_sq}, DuckDB={v_dk}"
        )

    def test_shopify_receita_total(self, sqlite_fed):
        """Receita total Shopify deve bater."""
        sql = "SELECT SUM(receita) AS total FROM shopify_receita_diaria"

        v_sq = query_fed(sql)[0]["total"] or 0
        v_dk = query_duck(sql)[0]["total"] or 0

        assert self._tolerancia(v_sq, v_dk), (
            f"Receita Shopify diverge: SQLite={v_sq:.2f}, DuckDB={v_dk:.2f}"
        )


# ── 4. Features exclusivas DuckDB ─────────────────────────────────────────────

class TestFeaturesDuckDB:

    def test_window_function_roas_7d(self):
        """Window function (média móvel 7d) — só disponível em DuckDB."""
        sql = """
            SELECT
                data_inicio,
                campanha,
                roas_compras,
                AVG(roas_compras) OVER (
                    PARTITION BY campanha
                    ORDER BY data_inicio
                    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                ) AS roas_7d_ma
            FROM campanhas_unificada
            WHERE cliente_key = 'source_of'
              AND plataforma = 'meta_ads'
              AND roas_compras IS NOT NULL
            ORDER BY data_inicio DESC
            LIMIT 10
        """
        rows = query_duck(sql)
        assert isinstance(rows, list)
        assert len(rows) > 0
        assert "roas_7d_ma" in rows[0]
        assert rows[0]["roas_7d_ma"] is not None

    def test_rank_campanhas_por_receita(self):
        """RANK() OVER — ranking de campanhas por receita, só DuckDB."""
        sql = """
            SELECT
                campanha,
                SUM(valor_compras) AS receita,
                RANK() OVER (ORDER BY SUM(valor_compras) DESC) AS rank_receita
            FROM campanhas_unificada
            WHERE cliente_key = 'source_of'
            GROUP BY campanha
            ORDER BY rank_receita
            LIMIT 5
        """
        rows = query_duck(sql)
        assert len(rows) > 0
        assert rows[0]["rank_receita"] == 1
        assert rows[0]["receita"] >= rows[-1]["receita"]

    def test_percentile_investimento(self):
        """PERCENTILE_CONT — percentil de investimento, só DuckDB."""
        sql = """
            SELECT
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY investimento) AS mediana_invest,
                PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY investimento) AS p90_invest
            FROM campanhas_unificada
            WHERE investimento > 0
        """
        rows = query_duck(sql)
        assert rows[0]["mediana_invest"] is not None
        assert rows[0]["p90_invest"] >= rows[0]["mediana_invest"]


# ── 5. API helpers ────────────────────────────────────────────────────────────

class TestAPIHelpers:

    def test_query_duck_retorna_list_dict(self):
        """query_duck() retorna list[dict] como query_fed()."""
        rows = query_duck("SELECT 1 AS a, 'x' AS b")
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)
        assert rows[0]["a"] == 1
        assert rows[0]["b"] == "x"

    def test_query_duck_com_params(self):
        """query_duck() aceita parâmetros posicionais."""
        rows = query_duck(
            "SELECT COUNT(*) AS n FROM campanhas_unificada WHERE cliente_key = ?",
            ("source_of",)
        )
        assert rows[0]["n"] > 0

    def test_query_duck_one_retorna_dict(self):
        """query_duck_one() retorna dict ou None."""
        row = query_duck_one(
            "SELECT SUM(investimento) AS total FROM campanhas_unificada "
            "WHERE cliente_key = ?",
            ("source_of",)
        )
        assert row is not None
        assert "total" in row
        assert row["total"] > 0

    def test_query_duck_one_sem_resultado(self):
        """query_duck_one() retorna None quando não há resultado."""
        row = query_duck_one(
            "SELECT * FROM campanhas_unificada WHERE cliente_key = 'nao_existe'"
        )
        assert row is None

    def test_reset_duck_conn_recria(self):
        """reset_duck_conn() força recriação e nova conexão funciona."""
        reset_duck_conn()
        # Após reset, próxima query deve funcionar normalmente
        rows = query_duck("SELECT COUNT(*) AS n FROM campanhas_unificada")
        assert rows[0]["n"] > 0
