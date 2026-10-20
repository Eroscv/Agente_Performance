"""
tests/test_bq_bot_suspect.py

Testa as duas camadas de não-regressão de is_bot_suspect no BigQuery:

Camada 2 — write_shopify (pedidos/receita):
  - is_bot_suspect deve ser None quando ausente no row (não 0)
  - None → NULL no BQ → COALESCE(NULL, T.is_bot_suspect) = T.is_bot_suspect (preserva)
  - Quando presente no row, deve passar o valor correto

Camada 3 — write_shopify_sessoes (MERGE direto de sessões):
  - SQL gerado deve conter GREATEST(COALESCE(...), COALESCE(...))
  - Nunca deve conter "T.is_bot_suspect = S.is_bot_suspect" simples
  - Valores são embutidos como literais SQL no UNNEST — verificar 0 e 1
  - Row com is_bot_suspect ausente → fallback para 0 no literal SQL
  - Row com is_bot_suspect=1 → literal SQL = 1

Todos os testes são unitários puros: sem conexão BQ, sem rede.
O SQL é interceptado via mock antes de ser enviado.
"""

import os
import sys
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _row_pedido(data="2026-10-08", **kwargs):
    """Row típico de write_shopify (receita/pedidos) — sem is_bot_suspect."""
    base = {
        "data": data,
        "receita": 5000.0,
        "frete": 100.0,
        "pedidos": 50,
        "novos_clientes": 20,
        "recompradores": 30,
        "ticket_medio": 100.0,
        "total_desconto": 200.0,
        "pedidos_acima_300": 10,
        "receita_criada": 5000.0,
        "pedidos_criados": 50,
        "pedidos_meta": 30,
        "receita_meta": 3000.0,
        "novos_meta": 10,
        "pedidos_google": 10,
        "receita_google": 1000.0,
        "novos_google": 5,
        "pedidos_outros": 10,
        "receita_outros": 1000.0,
        "novos_outros": 5,
        "gross_sales": 5200.0,
        "pedidos_gt300": 10,
        # Sessões — podem estar presentes ou ausentes
        "sessoes": None,
        "sessoes_com_carrinho": None,
        "sessoes_checkout": None,
        "sessoes_compra": None,
        "taxa_conversao": None,
        # is_bot_suspect deliberadamente AUSENTE (não incluído no dict)
    }
    base.update(kwargs)
    return base


def _row_sessao(data="2026-10-08", is_bot_suspect=0, **kwargs):
    """Row típico de write_shopify_sessoes."""
    base = {
        "data": data,
        "sessoes": 1500,
        "sessoes_com_carrinho": 300,
        "sessoes_checkout": 100,
        "sessoes_compra": 50,
        "taxa_conversao": 0.033,
        "is_bot_suspect": is_bot_suspect,
    }
    base.update(kwargs)
    return base


def _capturar_bq_rows(rows, cliente="source_of"):
    """
    Chama write_shopify com BQ habilitado (mock) e retorna os bq_rows montados.
    Intercepta _insert para capturar o payload antes do envio.
    """
    captured = {}

    def fake_insert(table_id, bq_rows_arg, desc):
        captured["rows"] = bq_rows_arg
        captured["table_id"] = table_id
        return len(bq_rows_arg)

    with patch("db.bq_writer.enabled", return_value=True), \
         patch("db.bq_writer._insert", side_effect=fake_insert), \
         patch.dict(os.environ, {"BQ_PROJECT": "proj", "BQ_DATASET": "ds"}):
        from db.bq_writer import write_shopify
        write_shopify(rows, cliente=cliente)

    return captured.get("rows", [])


def _capturar_merge_sql(rows, cliente="source_of"):
    """
    Chama write_shopify_sessoes com BQ mock e retorna o SQL MERGE gerado.
    """
    captured_sql = {}

    mock_query_result = MagicMock()
    mock_query_result.result.return_value = None
    mock_bq = MagicMock()
    mock_bq.project = "proj"
    mock_bq.query.return_value = mock_query_result

    def fake_query(sql):
        captured_sql["sql"] = sql
        return mock_query_result

    mock_bq.query.side_effect = fake_query

    with patch("db.bq_writer.enabled", return_value=True), \
         patch("db.bq_writer._client", return_value=mock_bq), \
         patch.dict(os.environ, {"BQ_PROJECT": "proj", "BQ_DATASET": "ds"}):
        from db.bq_writer import write_shopify_sessoes
        write_shopify_sessoes(rows, cliente=cliente)

    return captured_sql.get("sql", "")


# ---------------------------------------------------------------------------
# CAMADA 2 — write_shopify (pedidos): is_bot_suspect como None
# ---------------------------------------------------------------------------

class TestWriteShopifyBotSuspect:

    def test_is_bot_suspect_ausente_vira_none_no_payload(self):
        """Row sem is_bot_suspect deve gerar None no bq_row — não 0."""
        rows = [_row_pedido("2026-10-08")]
        assert "is_bot_suspect" not in rows[0], "pré-condição: campo ausente no row"

        bq_rows = _capturar_bq_rows(rows)
        assert bq_rows, "deve ter gerado bq_rows"
        assert bq_rows[0]["is_bot_suspect"] is None, \
            f"esperado None, obtido {bq_rows[0]['is_bot_suspect']!r}"

    def test_is_bot_suspect_none_explicito_vira_none(self):
        """Row com is_bot_suspect=None explícito também deve vira None."""
        rows = [_row_pedido("2026-10-08", is_bot_suspect=None)]
        bq_rows = _capturar_bq_rows(rows)
        assert bq_rows[0]["is_bot_suspect"] is None

    def test_is_bot_suspect_1_preservado(self):
        """Quando o row traz is_bot_suspect=1, deve chegar como 1 no BQ."""
        rows = [_row_pedido("2026-10-08", is_bot_suspect=1)]
        bq_rows = _capturar_bq_rows(rows)
        assert bq_rows[0]["is_bot_suspect"] == 1

    def test_is_bot_suspect_0_preservado(self):
        """Quando o row traz is_bot_suspect=0, deve chegar como 0."""
        rows = [_row_pedido("2026-10-08", is_bot_suspect=0)]
        bq_rows = _capturar_bq_rows(rows)
        assert bq_rows[0]["is_bot_suspect"] == 0

    def test_multiplos_rows_bot_suspect_none(self):
        """Múltiplos rows sem is_bot_suspect: todos None."""
        rows = [_row_pedido(f"2026-10-{d:02d}") for d in range(1, 6)]
        bq_rows = _capturar_bq_rows(rows)
        assert len(bq_rows) == 5
        for r in bq_rows:
            assert r["is_bot_suspect"] is None, \
                f"dia {r['data']}: esperado None, obtido {r['is_bot_suspect']!r}"

    def test_mix_rows_com_e_sem_bot_suspect(self):
        """Mix: alguns com flag, outros sem. Cada um segue sua regra."""
        rows = [
            _row_pedido("2026-10-07"),                      # sem campo → None
            _row_pedido("2026-10-08", is_bot_suspect=1),    # com 1 → 1
            _row_pedido("2026-10-09", is_bot_suspect=0),    # com 0 → 0
            _row_pedido("2026-10-10", is_bot_suspect=None), # None explícito → None
        ]
        bq_rows = _capturar_bq_rows(rows)
        by_date = {r["data"]: r["is_bot_suspect"] for r in bq_rows}
        assert by_date["2026-10-07"] is None
        assert by_date["2026-10-08"] == 1
        assert by_date["2026-10-09"] == 0
        assert by_date["2026-10-10"] is None

    def test_cenario_real_metricas_site_sem_campo(self):
        """
        metricas_site() não retorna is_bot_suspect — simula o que o cron_shopify_sync manda.
        Deve gerar None → BQ COALESCE preserva o 1 existente.
        """
        # metricas_site() retorna sessões mas sem is_bot_suspect
        row_metricas = {
            "data": "2026-10-08",
            "receita": 15000.0,
            "frete": 0.0,
            "pedidos": 89,
            "novos_clientes": 40,
            "recompradores": 49,
            "ticket_medio": 168.0,
            "total_desconto": 500.0,
            "pedidos_acima_300": 20,
            "receita_criada": 15000.0,
            "pedidos_criados": 89,
            "pedidos_meta": 50,
            "receita_meta": 8000.0,
            "novos_meta": 20,
            "pedidos_google": 20,
            "receita_google": 4000.0,
            "novos_google": 10,
            "pedidos_outros": 19,
            "receita_outros": 3000.0,
            "novos_outros": 10,
            "gross_sales": 15500.0,
            "pedidos_gt300": 20,
            "sessoes": None,        # metricas_site pode retornar None
            "sessoes_com_carrinho": None,
            "sessoes_checkout": None,
            "sessoes_compra": None,
            "taxa_conversao": None,
            # is_bot_suspect AUSENTE — como metricas_site() retorna
        }
        bq_rows = _capturar_bq_rows([row_metricas])
        assert bq_rows[0]["is_bot_suspect"] is None, \
            "metricas_site sem is_bot_suspect deve gerar None → COALESCE preserva T.is_bot_suspect=1 no BQ"


# ---------------------------------------------------------------------------
# CAMADA 3 — write_shopify_sessoes: GREATEST no SQL
# ---------------------------------------------------------------------------

class TestWriteShopifySessoesMergeSQL:

    def test_sql_contem_greatest_coalesce(self):
        """O MERGE gerado deve usar GREATEST(COALESCE(...), COALESCE(...)) para is_bot_suspect."""
        rows = [_row_sessao("2026-10-08", is_bot_suspect=0)]
        sql = _capturar_merge_sql(rows)
        assert sql, "SQL deve ter sido capturado"
        assert "GREATEST" in sql, "SQL deve conter GREATEST"
        assert "COALESCE" in sql, "SQL deve conter COALESCE"
        assert "T.is_bot_suspect" in sql and "S.is_bot_suspect" in sql

    def test_sql_nao_contem_sobrescrita_direta(self):
        """SQL não deve ter 'T.is_bot_suspect = S.is_bot_suspect' simples."""
        rows = [_row_sessao("2026-10-08", is_bot_suspect=0)]
        sql = _capturar_merge_sql(rows)
        # Garante que não há atribuição direta sem GREATEST
        assert "T.is_bot_suspect       = S.is_bot_suspect" not in sql, \
            "Sobrescrita direta encontrada — regressão possível"

    def test_sql_greatest_formula_correta(self):
        """Verifica a fórmula exata: GREATEST(COALESCE(T.is_bot_suspect, 0), COALESCE(S.is_bot_suspect, 0))"""
        rows = [_row_sessao("2026-10-08", is_bot_suspect=1)]
        sql = _capturar_merge_sql(rows)
        pattern = r"GREATEST\s*\(\s*COALESCE\s*\(\s*T\.is_bot_suspect\s*,\s*0\s*\)\s*,\s*COALESCE\s*\(\s*S\.is_bot_suspect\s*,\s*0\s*\)\s*\)"
        assert re.search(pattern, sql), \
            f"Fórmula GREATEST não encontrada no SQL:\n{sql}"

    def test_row_bot_suspect_1_literal_sql_correto(self):
        """Row com is_bot_suspect=1 → literal '1' no VALUES do UNNEST."""
        rows = [_row_sessao("2026-10-08", is_bot_suspect=1)]
        sql = _capturar_merge_sql(rows)
        # O valor é embutido no VALUES: (..., 1)
        assert re.search(r"'2026-10-08'.*,\s*1\s*\)", sql), \
            f"Esperado literal 1 para 2026-10-08 no SQL:\n{sql}"

    def test_row_bot_suspect_0_literal_sql_correto(self):
        """Row com is_bot_suspect=0 → literal '0' no VALUES."""
        rows = [_row_sessao("2026-10-09", is_bot_suspect=0)]
        sql = _capturar_merge_sql(rows)
        assert re.search(r"'2026-10-09'.*,\s*0\s*\)", sql), \
            f"Esperado literal 0 para 2026-10-09 no SQL:\n{sql}"

    def test_row_sem_bot_suspect_fallback_0(self):
        """Row sem is_bot_suspect → fallback para 0 (int(None or 0))."""
        row = _row_sessao("2026-10-07")
        del row["is_bot_suspect"]  # remove o campo
        sql = _capturar_merge_sql([row])
        assert re.search(r"'2026-10-07'.*,\s*0\s*\)", sql), \
            f"Row sem is_bot_suspect deve gerar literal 0 no SQL:\n{sql}"

    def test_multiplos_rows_sql_correto(self):
        """Múltiplos rows: dia bot=1 e dia normal=0 — ambos no SQL com valores corretos."""
        rows = [
            _row_sessao("2026-10-08", is_bot_suspect=1, sessoes=8923),
            _row_sessao("2026-10-09", is_bot_suspect=0, sessoes=1500),
        ]
        sql = _capturar_merge_sql(rows)
        assert "GREATEST" in sql
        assert re.search(r"'2026-10-08'.*,\s*1\s*\)", sql), "08/10 deve ter literal 1"
        assert re.search(r"'2026-10-09'.*,\s*0\s*\)", sql), "09/10 deve ter literal 0"

    def test_rows_vazio_nao_chama_bq(self):
        """Lista vazia não deve gerar query BQ."""
        sql = _capturar_merge_sql([])
        assert sql == "", "Lista vazia não deve gerar SQL"

    def test_row_sem_data_ignorado(self):
        """Row sem 'data' é ignorado — não aparece no SQL como literal DATE '...'."""
        rows = [
            {"sessoes": 1000, "is_bot_suspect": 0},  # sem data
            _row_sessao("2026-10-10", is_bot_suspect=0),
        ]
        sql = _capturar_merge_sql(rows)
        assert "2026-10-10" in sql
        # Row sem data não gerou literal DATE 'YYYY-MM-DD' no UNNEST — apenas 1 data no VALUES
        date_literals = re.findall(r"DATE '\d{4}-\d{2}-\d{2}'", sql)
        assert len(date_literals) == 1, \
            f"Esperado 1 literal DATE (só 2026-10-10), encontrado {len(date_literals)}: {date_literals}"

    def test_sql_estrutura_merge_completa(self):
        """SQL gerado deve ter MERGE, USING, ON, WHEN MATCHED, WHEN NOT MATCHED."""
        rows = [_row_sessao("2026-10-08", is_bot_suspect=1)]
        sql = _capturar_merge_sql(rows)
        for keyword in ["MERGE", "USING", "ON T.cliente", "WHEN MATCHED", "WHEN NOT MATCHED", "INSERT"]:
            assert keyword in sql, f"Keyword '{keyword}' ausente no SQL"


# ---------------------------------------------------------------------------
# INTEGRAÇÃO — Camadas 2 + 3 juntas: cenário real completo
# ---------------------------------------------------------------------------

class TestNaoRegressaoIntegrada:

    def test_camada2_none_implica_coalesce_preserva(self):
        """
        Camada 2: write_shopify manda None para is_bot_suspect.
        O MERGE de pedidos usa COALESCE(S.is_bot_suspect, T.is_bot_suspect).
        Verificamos que o payload chega com None (pré-condição do COALESCE funcionar).
        """
        # Row de pedidos sem is_bot_suspect (como metricas_site retorna)
        rows = [_row_pedido("2026-10-08")]
        bq_rows = _capturar_bq_rows(rows)
        bot_val = bq_rows[0]["is_bot_suspect"]
        assert bot_val is None, \
            f"COALESCE só funciona com NULL. Recebeu: {bot_val!r}"

    def test_camada3_greatest_implica_nao_regressao_matematica(self):
        """
        Camada 3: GREATEST(COALESCE(T,0), COALESCE(S,0)) — verificação matemática.
        Todos os casos possíveis de T e S:
          T=1, S=0 → 1  (não regride)
          T=1, S=1 → 1  (mantém)
          T=0, S=0 → 0  (normal)
          T=0, S=1 → 1  (novo flag)
          T=NULL, S=1 → 1
          T=1, S=NULL → 1
          T=NULL, S=NULL → 0 (não existe na prática)
        """
        def greatest_coalesce(t, s):
            return max(t if t is not None else 0, s if s is not None else 0)

        casos = [
            (1, 0, 1, "T=1,S=0 → não regride"),
            (1, 1, 1, "T=1,S=1 → mantém"),
            (0, 0, 0, "T=0,S=0 → normal"),
            (0, 1, 1, "T=0,S=1 → novo flag"),
            (None, 1, 1, "T=NULL,S=1 → primeiro flag"),
            (1, None, 1, "T=1,S=NULL → preserva"),
            (None, None, 0, "T=NULL,S=NULL → sem flag"),
        ]
        for t, s, esperado, descricao in casos:
            resultado = greatest_coalesce(t, s)
            assert resultado == esperado, \
                f"GREATEST({t},{s}): esperado {esperado}, obtido {resultado} ({descricao})"

    def test_cenario_real_08out_camada3_sql(self):
        """
        Replica o cenário reportado: sync envia is_bot_suspect=0 para 08/10.
        O SQL gerado deve ter GREATEST que preservaria o 1 no BQ.
        """
        rows = [_row_sessao("2026-10-08", is_bot_suspect=0, sessoes=8923)]
        sql = _capturar_merge_sql(rows)
        # Garante que GREATEST está presente — se T.is_bot_suspect=1 no BQ, GREATEST(1,0)=1
        assert "GREATEST" in sql
        # O valor enviado é 0 (pior caso — como vinha antes do fix do SQLite)
        assert re.search(r"'2026-10-08'.*,\s*0\s*\)", sql), \
            "Valor 0 deve estar no SQL — GREATEST no BQ é que preserva o 1"

    def test_camada2_e_camada3_independentes(self):
        """
        Camada 2 (write_shopify) e Camada 3 (write_shopify_sessoes) são independentes.
        Camada 2 manda None → COALESCE no _insert MERGE.
        Camada 3 manda valor direto → GREATEST no write_shopify_sessoes MERGE.
        """
        # Camada 2
        rows_pedidos = [_row_pedido("2026-10-08")]
        bq_rows = _capturar_bq_rows(rows_pedidos)
        assert bq_rows[0]["is_bot_suspect"] is None, "Camada 2: deve ser None"

        # Camada 3
        rows_sessoes = [_row_sessao("2026-10-08", is_bot_suspect=0)]
        sql = _capturar_merge_sql(rows_sessoes)
        assert "GREATEST" in sql, "Camada 3: deve ter GREATEST no SQL"
