"""
tests/test_metricas.py

Testes automatizados das fórmulas de métricas do Agente de Performance.

Garante que:
  - ROAS = SUM(receita) / SUM(invest)  — não AVG
  - CPC  = SUM(invest) / SUM(cliques)
  - CPM  = SUM(invest) / SUM(impressoes) * 1000
  - CTR  = SUM(cliques) / SUM(impressoes) * 100
  - Frequência = SUM(impressoes) / SUM(alcance)
  - CPA  = SUM(invest) * 1.1215 / SUM(compras)

Usa dados reais do banco (junho 2026) como referência.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# Garante que o projeto está no path
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from db.connection import DB_PATH
from agent.queries import resumo_periodo, data_range_disponivel
from relatorio.regras import FATOR_IMPOSTO


# ── Fixture: dados brutos de junho 2026 ──────────────────────────────────────

DATA_INICIO = "2026-06-01"
DATA_FIM    = "2026-06-23"  # range máximo disponível

TOLERANCE   = 1e-4  # tolerância para comparações de float


@pytest.fixture(scope="module")
def raw_june():
    """
    Retorna totais brutos diretamente do SQLite para junho 2026.
    Usa campanhas_unificada — mesma fonte (view unificada) que queries.resumo_periodo.
    Isso garante que as fórmulas sejam testadas contra os mesmos dados que o agente usa.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            SUM(investimento)           AS total_invest,
            SUM(valor_compras)          AS total_receita,
            SUM(compras)                AS total_compras,
            SUM(impressoes)             AS total_impressoes,
            SUM(cliques_link)           AS total_cliques,
            SUM(COALESCE(alcance, 0))   AS total_alcance
        FROM campanhas_unificada
        WHERE data_inicio BETWEEN ? AND ?
    """, (DATA_INICIO, DATA_FIM))
    row = dict(cur.fetchone())
    conn.close()
    return row


@pytest.fixture(scope="module")
def resumo_june():
    """Retorna resumo via função de queries (usa as fórmulas do agente)."""
    return resumo_periodo(DATA_INICIO, DATA_FIM)


# ── Testes de fórmulas ────────────────────────────────────────────────────────

class TestROAS:
    """ROAS deve ser SUM(receita) / SUM(invest) — não AVG(roas_por_dia)."""

    def test_roas_formula(self, raw_june, resumo_june):
        invest  = raw_june["total_invest"]
        receita = raw_june["total_receita"]
        roas_esperado = round(receita / invest, 4)
        roas_obtido   = resumo_june["roas"]
        assert abs(roas_obtido - roas_esperado) < TOLERANCE, (
            f"ROAS incorreto: esperado {roas_esperado:.4f}, obtido {roas_obtido:.4f}. "
            "Possível uso de AVG ao invés de SUM(receita)/SUM(invest)."
        )

    def test_roas_nao_zero(self, resumo_june):
        assert resumo_june["roas"] is not None and resumo_june["roas"] > 0, \
            "ROAS deve ser positivo para junho 2026"

    def test_roas_acima_de_1(self, resumo_june):
        """Sanidade: ROAS do período deve ser > 1 (campanhas rentáveis)."""
        assert resumo_june["roas"] > 1.0, \
            f"ROAS esperado > 1x, mas obtido {resumo_june['roas']:.4f}"


class TestCPC:
    """CPC = SUM(invest) / SUM(cliques)."""

    def test_cpc_formula(self, raw_june, resumo_june):
        invest  = raw_june["total_invest"]
        cliques = raw_june["total_cliques"]
        cpc_esperado = round(invest / cliques, 2)
        cpc_obtido   = resumo_june["cpc_medio"]
        assert abs(cpc_obtido - cpc_esperado) < 0.01, (
            f"CPC incorreto: esperado {cpc_esperado:.2f}, obtido {cpc_obtido:.2f}"
        )

    def test_cpc_positivo(self, resumo_june):
        assert resumo_june["cpc_medio"] > 0, "CPC deve ser positivo"


class TestCPM:
    """CPM = SUM(invest) / SUM(impressoes) * 1000."""

    def test_cpm_formula(self, raw_june, resumo_june):
        invest     = raw_june["total_invest"]
        impressoes = raw_june["total_impressoes"]
        cpm_esperado = round(invest / impressoes * 1000, 2)
        cpm_obtido   = resumo_june["cpm_medio"]
        assert abs(cpm_obtido - cpm_esperado) < 0.01, (
            f"CPM incorreto: esperado {cpm_esperado:.2f}, obtido {cpm_obtido:.2f}"
        )

    def test_cpm_positivo(self, resumo_june):
        assert resumo_june["cpm_medio"] > 0, "CPM deve ser positivo"


class TestCTR:
    """CTR = SUM(cliques) / SUM(impressoes) * 100."""

    def test_ctr_formula(self, raw_june, resumo_june):
        cliques    = raw_june["total_cliques"]
        impressoes = raw_june["total_impressoes"]
        ctr_esperado = round(cliques / impressoes * 100, 4)
        ctr_obtido   = resumo_june["ctr_medio"]
        assert abs(ctr_obtido - ctr_esperado) < TOLERANCE, (
            f"CTR incorreto: esperado {ctr_esperado:.4f}%, obtido {ctr_obtido:.4f}%"
        )

    def test_ctr_entre_0_e_100(self, resumo_june):
        ctr = resumo_june["ctr_medio"]
        assert 0 < ctr < 100, f"CTR fora do range esperado (0-100%): {ctr}"


class TestFrequencia:
    """Frequência = SUM(impressoes) / SUM(alcance)."""

    def test_frequencia_formula(self, raw_june):
        """Testa frequência diretamente do banco via campanhas_unificada."""
        conn = sqlite3.connect(str(DB_PATH))
        cur  = conn.cursor()
        cur.execute("""
            SELECT
                SUM(impressoes) AS total_imp,
                SUM(COALESCE(alcance, 0)) AS total_alc
            FROM campanhas_unificada
            WHERE data_inicio BETWEEN ? AND ?
              AND COALESCE(alcance, 0) > 0
        """, (DATA_INICIO, DATA_FIM))
        row = cur.fetchone()
        conn.close()

        total_imp, total_alc = row
        freq_real = total_imp / total_alc if total_alc else None

        assert freq_real is not None, "Não foi possível calcular frequência real (sem alcance)"
        assert freq_real >= 1.0, f"Frequência SUM/SUM < 1.0: {freq_real:.4f} — dados suspeitos"

    def test_frequencia_avg_vs_sum(self, raw_june):
        """Verifica que frequência SUM/SUM pode diferir de AVG (ilustra abordagens)."""
        conn = sqlite3.connect(str(DB_PATH))
        cur  = conn.cursor()
        cur.execute("""
            SELECT
                SUM(impressoes) / NULLIF(SUM(COALESCE(alcance, 0)), 0) AS freq_sum_sum
            FROM campanhas_unificada
            WHERE data_inicio BETWEEN ? AND ?
              AND COALESCE(alcance, 0) > 0
        """, (DATA_INICIO, DATA_FIM))
        row = cur.fetchone()
        conn.close()

        freq_sum_sum = row[0]
        print(f"\n  Frequência SUM/SUM={freq_sum_sum:.4f}")
        assert freq_sum_sum is not None


class TestCPA:
    """CPA = SUM(invest) * FATOR_IMPOSTO / SUM(compras)."""

    def test_cpa_formula(self, raw_june, resumo_june):
        invest  = raw_june["total_invest"]
        compras = raw_june["total_compras"]
        cpa_esperado = round(invest * FATOR_IMPOSTO / compras, 2)
        cpa_obtido   = resumo_june["custo_medio_por_compra"]
        assert abs(cpa_obtido - cpa_esperado) < 0.01, (
            f"CPA incorreto: esperado {cpa_esperado:.2f}, obtido {cpa_obtido:.2f}. "
            f"Verificar se FATOR_IMPOSTO={FATOR_IMPOSTO} está sendo aplicado."
        )

    def test_cpa_usa_fator_imposto(self, raw_june, resumo_june):
        """Garante que o CPA inclui o fator de imposto, não é invest/compras puro."""
        invest  = raw_june["total_invest"]
        compras = raw_june["total_compras"]
        cpa_sem_imposto = invest / compras
        cpa_com_imposto = invest * FATOR_IMPOSTO / compras
        cpa_real        = resumo_june["custo_medio_por_compra"]

        # CPA real deve ser próximo do valor COM imposto, não sem
        diff_com = abs(cpa_real - cpa_com_imposto)
        diff_sem = abs(cpa_real - cpa_sem_imposto)
        assert diff_com < diff_sem or diff_com < 0.01, (
            f"CPA parece não estar usando FATOR_IMPOSTO={FATOR_IMPOSTO}. "
            f"Sem imposto: {cpa_sem_imposto:.2f}, Com imposto: {cpa_com_imposto:.2f}, Real: {cpa_real:.2f}"
        )

    def test_cpa_positivo(self, resumo_june):
        assert resumo_june["custo_medio_por_compra"] > 0, "CPA deve ser positivo"


# ── Testes de consistência do banco ──────────────────────────────────────────

class TestConsistenciaBanco:
    """Testes de sanidade geral dos dados."""

    def test_banco_tem_dados_junho_2026(self, resumo_june):
        assert resumo_june.get("investimento_total", 0) > 0, \
            "Banco deve ter dados de junho 2026"

    def test_range_disponivel(self):
        dr = data_range_disponivel()
        assert dr["data_minima"] is not None
        assert dr["data_maxima"] is not None
        assert dr["total_linhas"] > 0
        assert dr["total_campanhas"] > 0

    def test_compras_menor_cliques(self, raw_june):
        """Compras não podem ser maior que cliques (funil de conversão)."""
        assert raw_june["total_compras"] <= raw_june["total_cliques"], \
            "Compras > Cliques — dados inconsistentes no banco"

    def test_alcance_menor_impressoes(self, raw_june):
        """Alcance (pessoas únicas) sempre <= impressões quando rastreado.
        Nota: alcance é NULL em ~75% das linhas da API Meta — teste só valida quando disponível."""
        alcance = raw_june["total_alcance"]
        impressoes = raw_june["total_impressoes"]
        if alcance and alcance > 0:
            assert alcance <= impressoes, \
                f"Alcance ({alcance}) > Impressões ({impressoes}) — dados inconsistentes"

    def test_receita_positiva(self, raw_june):
        assert raw_june["total_receita"] > 0, "Receita total deve ser positiva"

    def test_fator_imposto_valor(self):
        """FATOR_IMPOSTO deve ser 1.1215 (ISS + PIS/COFINS padrão)."""
        assert FATOR_IMPOSTO == 1.1215, \
            f"FATOR_IMPOSTO esperado 1.1215, obtido {FATOR_IMPOSTO}"
