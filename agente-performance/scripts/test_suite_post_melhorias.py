"""
scripts/test_suite_post_melhorias.py

Suite de testes pós-melhorias do Agente de Performance Guará.
Cobre todos os tipos de consulta: resumo, ranking, funil, comparação, alertas,
evolução diária, campanha específica, comparação de períodos WoW/MoM, etc.

Dados reais disponíveis: 2024-01-23 → 2026-06-23 | 32 campanhas | 28.256 registros

Uso:
    python scripts/test_suite_post_melhorias.py
    python scripts/test_suite_post_melhorias.py --verbose
    python scripts/test_suite_post_melhorias.py --grupo roas
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.WARNING,  # silencia logs do agente durante os testes
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_suite")

# ── Imports do agente ─────────────────────────────────────────────────────────
from agent import queries
from agent.cache import invalidar_tudo
from agent.comparatives import (
    comparar_periodos,
    comparar_campanhas_detalhado,
    comparar_semana_a_semana,
    comparar_mes_a_mes,
    ranking_comparativo,
)
from relatorio.builder import gerar_relatorio_semanal, gerar_relatorio_mensal
from agent.engine import consultar as engine_consultar
from agent.cache import get as cache_get, set as cache_set, stats as cache_stats
from agent.context_store import cleanup_expired as ctx_cleanup
from datetime import date, timedelta


# ── Resultado de cada teste ───────────────────────────────────────────────────

@dataclass
class TestResult:
    nome: str
    grupo: str
    status: str  # PASS | FAIL | SKIP
    duracao_ms: int = 0
    detalhes: str = ""
    erro: str = ""

    def linha(self) -> str:
        icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(self.status, "?")
        dur = f"{self.duracao_ms}ms"
        return f"  {icon} [{self.grupo:20s}] {self.nome[:55]:<55s}  {dur:>6s}  {self.detalhes[:60]}"


# ── Runner ────────────────────────────────────────────────────────────────────

@dataclass
class Suite:
    verbose: bool = False
    filtro_grupo: str | None = None
    resultados: list[TestResult] = field(default_factory=list)

    def run(self, nome: str, grupo: str, fn: Callable[[], Any], validar: Callable[[Any], str] | None = None):
        if self.filtro_grupo and self.filtro_grupo.lower() not in grupo.lower():
            self.resultados.append(TestResult(nome, grupo, "SKIP"))
            return

        t = time.time()
        try:
            resultado = fn()
            duracao = int((time.time() - t) * 1000)

            # Validação básica: resultado não pode ser None ou lista/dict vazio
            detalhes = ""
            if resultado is None:
                raise AssertionError("Resultado é None")
            if isinstance(resultado, list):
                if len(resultado) == 0:
                    raise AssertionError("Lista vazia retornada")
                detalhes = f"{len(resultado)} registros"
            elif isinstance(resultado, dict):
                if not resultado:
                    raise AssertionError("Dict vazio retornado")
                # Resumo: mostra ROAS se disponível
                if "roas" in resultado:
                    detalhes = f"ROAS={resultado['roas']:.2f}x" if resultado["roas"] else "ROAS=—"
                elif "total_invest" in resultado:
                    detalhes = f"invest=R${resultado['total_invest']:,.0f}"
                else:
                    detalhes = f"{len(resultado)} campos"

            # Validação customizada
            if validar:
                msg = validar(resultado)
                if msg:
                    raise AssertionError(msg)

            r = TestResult(nome, grupo, "PASS", duracao, detalhes)
            if self.verbose:
                print(r.linha())

        except Exception as e:
            duracao = int((time.time() - t) * 1000)
            r = TestResult(nome, grupo, "FAIL", duracao, erro=str(e)[:120])
            print(r.linha())
            if self.verbose:
                traceback.print_exc()

        self.resultados.append(r)

    def resumo(self) -> int:
        total  = len(self.resultados)
        passed = sum(1 for r in self.resultados if r.status == "PASS")
        failed = sum(1 for r in self.resultados if r.status == "FAIL")
        skipped= sum(1 for r in self.resultados if r.status == "SKIP")
        dur_total = sum(r.duracao_ms for r in self.resultados)

        print()
        print("═" * 80)
        print(f"  RESULTADO FINAL: {passed}/{total - skipped} passed  |  {failed} failed  |  {skipped} skipped  |  {dur_total}ms total")
        print("═" * 80)

        if failed:
            print("\n  Falhas detalhadas:")
            for r in self.resultados:
                if r.status == "FAIL":
                    print(f"    ❌ [{r.grupo}] {r.nome}")
                    print(f"       → {r.erro}")
        print()
        return failed


# ── Datas de referência ───────────────────────────────────────────────────────
# Banco: 2024-01-23 → 2026-06-23

W_RECENTE_INI = "2026-06-16"   # semana 2026-W24 (última semana completa com dados)
W_RECENTE_FIM = "2026-06-22"
W_ANT_INI     = "2026-06-09"   # semana anterior
W_ANT_FIM     = "2026-06-15"

MES_RECENTE_INI = "2026-06-01"
MES_RECENTE_FIM = "2026-06-23"
MES_ANT_INI   = "2026-05-01"
MES_ANT_FIM   = "2026-05-31"

BLACK_NOV_INI = "2025-11-24"
BLACK_NOV_FIM = "2025-11-30"

Q2_2026_INI   = "2026-04-01"
Q2_2026_FIM   = "2026-06-23"

CAMP_TOP      = "ABO_Testes_Todos_Produtos"
CAMP_ADV      = "Advantage_TodosProdutos_09/2025"
CAMP_HIDRA    = "Advantage_HidratanteCorporal"


# ══════════════════════════════════════════════════════════════════════════════
# TESTES
# ══════════════════════════════════════════════════════════════════════════════

def registrar_todos(s: Suite):

    # ── GRUPO: RESUMO ─────────────────────────────────────────────────────────
    s.run("Resumo por campanha — sem filtro",          "RESUMO",
          lambda: queries.resumo_por_campanha(None, None),
          lambda r: "Menos de 10 campanhas" if len(r) < 10 else None)

    s.run("Resumo semana recente (2026-W24)",          "RESUMO",
          lambda: queries.resumo_por_campanha(W_RECENTE_INI, W_RECENTE_FIM))

    s.run("Resumo semana anterior (2026-W23)",         "RESUMO",
          lambda: queries.resumo_por_campanha(W_ANT_INI, W_ANT_FIM))

    s.run("Resumo período consolidado — junho/2026",   "RESUMO",
          lambda: queries.resumo_periodo(MES_RECENTE_INI, MES_RECENTE_FIM),
          lambda r: "ROAS ausente" if r.get("roas") is None else None)

    s.run("Resumo período consolidado — maio/2026",    "RESUMO",
          lambda: queries.resumo_periodo(MES_ANT_INI, MES_ANT_FIM))

    s.run("Resumo período — Q2 2026 (abr-jun)",        "RESUMO",
          lambda: queries.resumo_periodo(Q2_2026_INI, Q2_2026_FIM),
          lambda r: "investimento_total zerado" if not r.get("investimento_total") else None)

    s.run("Resumo período — Black Friday 2025",        "RESUMO",
          lambda: queries.resumo_periodo(BLACK_NOV_INI, BLACK_NOV_FIM))

    s.run("Resumo período — ano 2025 completo",        "RESUMO",
          lambda: queries.resumo_periodo("2025-01-01", "2025-12-31"),
          lambda r: "compras_total zerado em 2025" if not r.get("compras_total") else None)

    s.run("Resumo período — 2024 completo",            "RESUMO",
          lambda: queries.resumo_periodo("2024-01-23", "2024-12-31"))

    # ── GRUPO: ROAS ───────────────────────────────────────────────────────────
    s.run("Top 5 ROAS — semana recente",               "ROAS",
          lambda: queries.top_campanhas_roas(5, W_RECENTE_INI, W_RECENTE_FIM),
          lambda r: "Nenhuma campanha" if len(r) == 0 else None)

    s.run("Top 10 ROAS — junho/2026",                  "ROAS",
          lambda: queries.top_campanhas_roas(10, MES_RECENTE_INI, MES_RECENTE_FIM))

    s.run("Top 5 ROAS — sem filtro de período",        "ROAS",
          lambda: queries.top_campanhas_roas(5, None, None))

    s.run("Top 5 ROAS — Black Friday 2025",            "ROAS",
          lambda: queries.top_campanhas_roas(5, BLACK_NOV_INI, BLACK_NOV_FIM))

    s.run("Top 3 ROAS — Q2 2026",                      "ROAS",
          lambda: queries.top_campanhas_roas(3, Q2_2026_INI, Q2_2026_FIM),
          lambda r: "ROAS negativo?" if any((x.get("roas") or 0) < 0 for x in r) else None)

    s.run("Alertas ROAS baixo — sem filtro",           "ROAS",
          lambda: queries.alertas_roas_baixo())

    # ── GRUPO: COMPRAS ────────────────────────────────────────────────────────
    s.run("Top 5 compras — semana recente",            "COMPRAS",
          lambda: queries.top_campanhas_compras(5, W_RECENTE_INI, W_RECENTE_FIM))

    s.run("Top 5 compras — junho/2026",                "COMPRAS",
          lambda: queries.top_campanhas_compras(5, MES_RECENTE_INI, MES_RECENTE_FIM))

    s.run("Top 10 compras — 2025 completo",            "COMPRAS",
          lambda: queries.top_campanhas_compras(10, "2025-01-01", "2025-12-31"))

    s.run("Top 5 compras — Black Friday 2025",         "COMPRAS",
          lambda: queries.top_campanhas_compras(5, BLACK_NOV_INI, BLACK_NOV_FIM))

    # ── GRUPO: CAMPANHA ESPECÍFICA ────────────────────────────────────────────
    s.run(f"Performance ABO — sem filtro período",     "CAMPANHA",
          lambda: queries.performance_campanha(CAMP_TOP, None, None),
          lambda r: "Menos de 5 dias" if len(r) < 5 else None)

    s.run(f"Performance ABO — junho/2026",             "CAMPANHA",
          lambda: queries.performance_campanha(CAMP_TOP, MES_RECENTE_INI, MES_RECENTE_FIM))

    s.run(f"Performance Advantage — 2025 completo",   "CAMPANHA",
          lambda: queries.performance_campanha(CAMP_ADV, "2025-01-01", "2025-12-31"))

    s.run(f"Performance Hidratante — sem filtro",      "CAMPANHA",
          lambda: queries.performance_campanha(CAMP_HIDRA, None, None))

    s.run(f"Performance Hidratante — Black Friday",    "CAMPANHA",
          lambda: queries.performance_campanha(CAMP_HIDRA, BLACK_NOV_INI, BLACK_NOV_FIM))

    # ── GRUPO: FUNIL ──────────────────────────────────────────────────────────
    s.run("Funil geral — junho/2026",                  "FUNIL",
          lambda: queries.metricas_funil(None, MES_RECENTE_INI, MES_RECENTE_FIM),
          lambda r: "impressoes zerado" if not r.get("impressoes") else None)

    s.run("Funil geral — semana recente",              "FUNIL",
          lambda: queries.metricas_funil(None, W_RECENTE_INI, W_RECENTE_FIM))

    s.run(f"Funil ABO — junho/2026",                   "FUNIL",
          lambda: queries.metricas_funil(CAMP_TOP, MES_RECENTE_INI, MES_RECENTE_FIM))

    s.run(f"Funil ABO — 2025 completo",                "FUNIL",
          lambda: queries.metricas_funil(CAMP_TOP, "2025-01-01", "2025-12-31"))

    s.run("Funil geral — sem filtro",                  "FUNIL",
          lambda: queries.metricas_funil(None, None, None))

    # ── GRUPO: EVOLUÇÃO DIÁRIA ────────────────────────────────────────────────
    s.run("Evolução diária — ABO junho/2026",          "EVOLUCAO",
          lambda: queries.evolucao_diaria(CAMP_TOP, MES_RECENTE_INI, MES_RECENTE_FIM),
          lambda r: "Menos de 5 dias" if len(r) < 5 else None)

    s.run("Evolução diária — ABO semana recente",      "EVOLUCAO",
          lambda: queries.evolucao_diaria(CAMP_TOP, W_RECENTE_INI, W_RECENTE_FIM))

    s.run("Evolução diária — geral semana recente",    "EVOLUCAO",
          lambda: queries.evolucao_diaria(None, W_RECENTE_INI, W_RECENTE_FIM))

    s.run("Evolução diária — Advantage Q2 2026",       "EVOLUCAO",
          lambda: queries.evolucao_diaria(CAMP_ADV, Q2_2026_INI, Q2_2026_FIM))

    # ── GRUPO: COMPARAÇÃO WoW/MoM ─────────────────────────────────────────────
    s.run("Comparação semana a semana — ABO",          "COMPARA_WOW",
          lambda: comparar_semana_a_semana(CAMP_TOP),
          lambda r: "Sem semana_atual" if "semana_atual" not in r else None)

    s.run("Comparação semana a semana — geral",        "COMPARA_WOW",
          lambda: comparar_semana_a_semana(None))

    s.run("Comparação mês a mês — ABO",                "COMPARA_MOM",
          lambda: comparar_mes_a_mes(CAMP_TOP),
          lambda r: "Sem mes_atual" if "mes_atual" not in r else None)

    s.run("Comparação mês a mês — geral",              "COMPARA_MOM",
          lambda: comparar_mes_a_mes(None))

    # ── GRUPO: COMPARAÇÃO DE PERÍODOS ─────────────────────────────────────────
    s.run("Comparar períodos — jun vs mai 2026",       "COMPARA_PER",
          lambda: comparar_periodos(MES_ANT_INI, MES_ANT_FIM,
                                    MES_RECENTE_INI, MES_RECENTE_FIM, None),
          lambda r: "Sem periodo_a" if "periodo_a" not in r else None)

    s.run("Comparar períodos — W24 vs W23 (ABO)",      "COMPARA_PER",
          lambda: comparar_periodos(W_ANT_INI, W_ANT_FIM,
                                    W_RECENTE_INI, W_RECENTE_FIM, CAMP_TOP))

    s.run("Comparar períodos — Q1 vs Q2 2026",         "COMPARA_PER",
          lambda: comparar_periodos("2026-01-01", "2026-03-31",
                                    "2026-04-01", "2026-06-23", None))

    s.run("Comparar períodos — BF 2025 vs jun 2026",   "COMPARA_PER",
          lambda: comparar_periodos(BLACK_NOV_INI, BLACK_NOV_FIM,
                                    MES_RECENTE_INI, MES_RECENTE_FIM, None))

    s.run("Comparar períodos — 2024 vs 2025",          "COMPARA_PER",
          lambda: comparar_periodos("2024-01-01", "2024-12-31",
                                    "2025-01-01", "2025-12-31", None))

    # ── GRUPO: COMPARAÇÃO ENTRE CAMPANHAS ─────────────────────────────────────
    s.run("Comparar ABO vs Advantage — junho/2026",    "COMPARA_CAMP",
          lambda: comparar_campanhas_detalhado(
              [CAMP_TOP, CAMP_ADV], MES_RECENTE_INI, MES_RECENTE_FIM),
          lambda r: "Menos de 2 campanhas" if len(r) < 2 else None)

    s.run("Comparar ABO vs Hidratante — sem filtro",   "COMPARA_CAMP",
          lambda: comparar_campanhas_detalhado(
              [CAMP_TOP, CAMP_HIDRA], None, None))

    # ── GRUPO: RANKING ────────────────────────────────────────────────────────
    s.run("Ranking por ROAS — junho/2026",             "RANKING",
          lambda: ranking_comparativo("roas", MES_RECENTE_INI, MES_RECENTE_FIM),
          lambda r: "Menos de 5" if len(r) < 5 else None)

    s.run("Ranking por compras — Q2 2026",             "RANKING",
          lambda: ranking_comparativo("compras", Q2_2026_INI, Q2_2026_FIM))

    s.run("Ranking por investimento — 2025",           "RANKING",
          lambda: ranking_comparativo("investimento", "2025-01-01", "2025-12-31"))

    s.run("Ranking por CPC — semana recente",          "RANKING",
          lambda: ranking_comparativo("cpc", W_RECENTE_INI, W_RECENTE_FIM))

    s.run("Ranking por frequência — junho/2026",       "RANKING",
          lambda: ranking_comparativo("frequencia", MES_RECENTE_INI, MES_RECENTE_FIM))

    s.run("Ranking por CTR — Black Friday 2025",       "RANKING",
          lambda: ranking_comparativo("ctr", BLACK_NOV_INI, BLACK_NOV_FIM))

    # ── GRUPO: BUILDER (RELATÓRIO) ────────────────────────────────────────────
    s.run("Builder — relatório semanal W24 2026",      "BUILDER",
          lambda: gerar_relatorio_semanal(
              cliente="source_of",
              data_inicio=date(2026, 6, 16),
              data_fim=date(2026, 6, 22),
              publicar=False),
          lambda r: "Erro no builder" if "erro" in r else (
                    "resumo ausente" if not r.get("resumo") else None))

    s.run("Builder — relatório semanal W23 2026",      "BUILDER",
          lambda: gerar_relatorio_semanal(
              cliente="source_of",
              data_inicio=date(2026, 6, 9),
              data_fim=date(2026, 6, 15),
              publicar=False))

    s.run("Builder — relatório mensal junho/2026",     "BUILDER",
          lambda: gerar_relatorio_mensal(
              cliente="source_of",
              publicar=False),
          lambda r: "Erro no builder" if "erro" in r else None)

    s.run("Builder — relatório semanal Black Friday",  "BUILDER",
          lambda: gerar_relatorio_semanal(
              cliente="source_of",
              data_inicio=date(2025, 11, 24),
              data_fim=date(2025, 11, 30),
              publicar=False))

    # ── GRUPO: CACHE ──────────────────────────────────────────────────────────
    s.run("Cache L1+L2 — set e get",                   "CACHE",
          lambda: _test_cache_set_get())

    s.run("Cache — invalidar_tudo limpa L1",           "CACHE",
          lambda: _test_cache_invalidar())

    s.run("Cache — stats retorna estrutura correta",   "CACHE",
          lambda: _test_cache_stats())

    s.run("Cache — evict L1 não quebra com store vazio", "CACHE",
          lambda: _test_cache_evict_empty())

    # ── GRUPO: PERIODO_NOTA (nova melhoria #1) ────────────────────────────────
    s.run("periodo_nota — presente no retorno NLP",    "PERIODO_NOTA",
          lambda: _test_periodo_nota_propagacao())

    # ── GRUPO: LISTAR ─────────────────────────────────────────────────────────
    s.run("Listar campanhas — sem filtro",             "LISTAR",
          lambda: queries.listar_campanhas(),
          lambda r: "Menos de 10" if len(r) < 10 else None)

    s.run("Range disponível no banco",                 "LISTAR",
          lambda: queries.data_range_disponivel(),
          lambda r: "data_minima ausente" if not r.get("data_minima") else None)


# ── Helpers de teste específico ───────────────────────────────────────────────

def _test_cache_set_get() -> dict:
    key = "teste_suite_2026"
    payload = {"dados": [1, 2, 3], "intent": "resumo_geral", "meta": {}}
    cache_set(key, payload)
    result = cache_get(key)
    assert result is not None, "cache_get retornou None após set"
    assert result["intent"] == "resumo_geral", "Payload corrompido"
    return {"status": "ok", "roas": None}


def _test_cache_invalidar() -> dict:
    from agent.cache import _store
    cache_set("teste_inv", {"dados": [], "intent": "x", "meta": {}})
    n_antes = len(_store)
    invalidar_tudo()
    n_depois = len(_store)
    assert n_depois == 0, f"L1 não foi limpo: {n_depois} entradas restantes"
    return {"status": "ok", "n_removidas": n_antes, "roas": None}


def _test_cache_stats() -> dict:
    from agent.cache import stats
    s = stats()
    assert "l1_ativas" in s
    assert "l2_ativas" in s
    assert "ttl_segundos" in s
    return s


def _test_cache_evict_empty() -> dict:
    from agent.cache import _evict_l1_if_due, _store, _l1_last_cleanup
    import agent.cache as cache_mod
    # Força o cleanup mesmo sem esperar o intervalo
    cache_mod._l1_last_cleanup = 0.0
    n = _evict_l1_if_due()
    assert isinstance(n, int)
    return {"status": "ok", "evicted": n, "roas": None}


def _test_periodo_nota_propagacao() -> dict:
    """
    Verifica que a chave 'periodo_nota' existe no retorno do NLP mesmo
    quando não há ajuste (deve ser None, não KeyError).
    """
    from agent.nlp import interpretar
    campanhas = ["ABO_Testes_Todos_Produtos", "Advantage_TodosProdutos_09/2025"]
    resultado = interpretar("resumo de junho", campanhas)
    assert "periodo_nota" in resultado, "periodo_nota ausente no retorno do NLP"
    return {"status": "ok", "periodo_nota": resultado.get("periodo_nota"), "roas": None}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Suite de testes pós-melhorias — Agente de Performance Guará")
    parser.add_argument("--verbose", "-v", action="store_true", help="Exibe cada teste em tempo real")
    parser.add_argument("--grupo", "-g", default=None,
                        help="Filtra por grupo: RESUMO, ROAS, COMPRAS, CAMPANHA, FUNIL, EVOLUCAO, "
                             "COMPARA_WOW, COMPARA_MOM, COMPARA_PER, COMPARA_CAMP, RANKING, BUILDER, CACHE, PERIODO_NOTA, LISTAR")
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════════════════════════╗")
    print("║   AGENTE DE PERFORMANCE GUARÁ — Suite de Testes Pós-Melhorias              ║")
    print("║   Banco: 2024-01-23 → 2026-06-23 | 28.256 registros | 32 campanhas         ║")
    print("╚══════════════════════════════════════════════════════════════════════════════╝")
    print()

    # Limpa cache antes dos testes para garantir resultados frescos
    invalidar_tudo()
    ctx_cleanup()
    print("  🧹 Cache e contexto limpos.")
    print(f"  🔍 Grupo filtrado: {args.grupo or 'TODOS'}")
    print()

    if args.verbose:
        print("  Resultados em tempo real:")
        print("  " + "─" * 78)

    suite = Suite(verbose=args.verbose, filtro_grupo=args.grupo)
    registrar_todos(suite)

    if not args.verbose:
        # Exibe tudo junto no final
        print("  Todos os testes:")
        print("  " + "─" * 78)
        for r in suite.resultados:
            print(r.linha())

    n_falhas = suite.resumo()
    sys.exit(0 if n_falhas == 0 else 1)


if __name__ == "__main__":
    main()
