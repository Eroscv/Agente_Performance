#!/usr/bin/env python3
"""
scripts/avaliar_nlp.py

Mede a precisão de agent.nlp.interpretar() contra um corpus de perguntas
rotuladas manualmente (tests/fixtures/nlp_corpus.json).

Implementa a seção 4 ("NLP: teste de precisão, não só de exemplo") e a
seção 6 ("Regras de aceite por camada") de:
  Notion > Agente de Performance Guará > Metodologia de Teste de Skills

Métricas (metas da seção 6):
  - Acurácia de intent        >= 90%
  - Acurácia de cliente_key   >= 95%  (risco de vazamento de dado entre clientes)
  - Acurácia de período       >= 85%  (só sobre casos com expected_period_synonym)

Casos com known_gap=true são medidos e reportados À PARTE — eles documentam
bugs já identificados e ainda não corrigidos (ver nota de cada caso no
corpus), e não devem contaminar silenciosamente a acurácia "saudável" nem
ser escondidos.

IMPORTANTE — modo de execução:
  agent.nlp.interpretar() chama o LLM real (Gemini, com fallback para Claude)
  via agent.claude_client._call_llm(). Sem GEMINI_API_KEY / ANTHROPIC_API_KEY
  configuradas, TODAS as chamadas caem no fallback de regex
  (agent.nlp._fallback_regex), que é um caminho de código DIFERENTE do parser
  real — mais pobre (não detecta cliente_key/plataforma de jeito nenhum, ver
  agent/engine.py::_detect_intent/_extract_dates). Este script detecta a
  ausência das chaves e avisa no cabeçalho do relatório; os números só
  valem como "precisão real do NLP" quando rodados com uma API key válida.

Uso:
  python3 scripts/avaliar_nlp.py
  python3 scripts/avaliar_nlp.py --corpus tests/fixtures/nlp_corpus.json --out tests/fixtures/nlp_eval_report.json
  python3 scripts/avaliar_nlp.py --fail-under   # exit code 1 se meta não bater (p/ CI, seção 7)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

METAS = {
    "intent":   0.90,
    "cliente":  0.95,
    "periodo":  0.85,
}


def _modo_execucao() -> str:
    """Detecta se há API key configurada — define se o teste mede o NLP real
    ou o fallback de regex degradado."""
    if os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("ANTHROPIC_API_KEY", "").strip():
        return "llm_real"
    return "fallback_regex"


def _resolver_periodo_esperado(chave_sinonimo):
    """Resolve um expected_period_synonym em (data_inicio, data_fim) usando
    agent.synonyms.PERIOD_SYNONYMS — evita hardcode de datas (evitaria
    flakiness, já que 'essa semana' etc. dependem do dia em que o script roda)."""
    if not chave_sinonimo:
        return None, None
    from agent.synonyms import PERIOD_SYNONYMS
    periodo = PERIOD_SYNONYMS.get(chave_sinonimo)
    if not periodo:
        return None, None
    partes = periodo.split(" a ")
    if len(partes) == 2:
        return partes[0].strip(), partes[1].strip()
    return None, None


def avaliar(corpus_path: Path) -> dict:
    from agent.nlp import interpretar

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    casos = corpus["casos"]
    modo = _modo_execucao()

    resultados = []
    for caso in casos:
        pergunta = caso["pergunta"]
        try:
            r = interpretar(pergunta, campanhas_disponiveis=[], contexto="")
        except Exception as e:
            r = {"erro_execucao": str(e), "intents": [], "cliente_key": None,
                 "plataforma": None, "data_inicio": None, "data_fim": None}

        intents_obtidas = r.get("intents") or []
        intent_ok = caso["expected_intent"] in intents_obtidas

        cliente_ok = r.get("cliente_key") == caso["expected_cliente_key"]
        plataforma_ok = r.get("plataforma") == caso["expected_plataforma"]

        periodo_checado = caso.get("expected_period_synonym") is not None
        periodo_ok = None
        if periodo_checado:
            exp_ini, exp_fim = _resolver_periodo_esperado(caso["expected_period_synonym"])
            periodo_ok = (r.get("data_inicio") == exp_ini) and (r.get("data_fim") == exp_fim)

        resultados.append({
            "id": caso["id"],
            "pergunta": pergunta,
            "known_gap": caso.get("known_gap", False),
            "intent_ok": intent_ok,
            "cliente_ok": cliente_ok,
            "plataforma_ok": plataforma_ok,
            "periodo_checado": periodo_checado,
            "periodo_ok": periodo_ok,
            "esperado": {
                "intent": caso["expected_intent"],
                "cliente_key": caso["expected_cliente_key"],
                "plataforma": caso["expected_plataforma"],
            },
            "obtido": {
                "intents": intents_obtidas,
                "cliente_key": r.get("cliente_key"),
                "plataforma": r.get("plataforma"),
                "data_inicio": r.get("data_inicio"),
                "data_fim": r.get("data_fim"),
            },
        })

    def _pct(cond_list):
        vals = [c for c in cond_list if c is not None]
        return (sum(1 for v in vals if v) / len(vals)) if vals else None

    normais = [r for r in resultados if not r["known_gap"]]
    gaps = [r for r in resultados if r["known_gap"]]

    relatorio = {
        "modo_execucao": modo,
        "total_casos": len(resultados),
        "casos_normais": len(normais),
        "casos_known_gap": len(gaps),
        "acuracia": {
            "intent":   _pct([r["intent_ok"] for r in normais]),
            "cliente":  _pct([r["cliente_ok"] for r in normais]),
            "periodo":  _pct([r["periodo_ok"] for r in normais if r["periodo_checado"]]),
        },
        "metas": METAS,
        "known_gaps_status": [
            {"id": r["id"], "intent_ok": r["intent_ok"], "cliente_ok": r["cliente_ok"],
             "plataforma_ok": r["plataforma_ok"], "obtido": r["obtido"]}
            for r in gaps
        ],
        "falhas_casos_normais": [
            r for r in normais
            if not r["intent_ok"] or not r["cliente_ok"] or not r["plataforma_ok"]
            or (r["periodo_checado"] and not r["periodo_ok"])
        ],
        "resultados_completos": resultados,
    }
    return relatorio


def imprimir_relatorio(rel: dict) -> bool:
    """Imprime relatório legível. Retorna True se todas as metas bateram."""
    modo = rel["modo_execucao"]
    print("=" * 72)
    print("AVALIAÇÃO DE PRECISÃO — agent.nlp.interpretar()")
    print("=" * 72)
    if modo == "fallback_regex":
        print("⚠️  MODO: fallback_regex (sem GEMINI_API_KEY/ANTHROPIC_API_KEY neste")
        print("    ambiente) — isto NÃO mede a precisão do parser Claude/Gemini real,")
        print("    mede agent.nlp._fallback_regex() + agent.engine._detect_intent().")
        print("    Rodar de novo com uma API key configurada para números de produção.")
    else:
        print("✅ MODO: llm_real (API key detectada)")
    print()
    print(f"Casos totais: {rel['total_casos']}  "
          f"(normais: {rel['casos_normais']}, known_gap: {rel['casos_known_gap']})")
    print()

    tudo_ok = True
    for campo, meta in rel["metas"].items():
        acc = rel["acuracia"][campo]
        if acc is None:
            print(f"  {campo:10s}: sem casos aplicáveis no corpus")
            continue
        status = "✅" if acc >= meta else "❌"
        if acc < meta:
            tudo_ok = False
        print(f"  {campo:10s}: {acc*100:5.1f}%  (meta ≥{meta*100:.0f}%)  {status}")

    if rel["known_gaps_status"]:
        print()
        print("Known gaps (bugs já identificados, não contam na acurácia acima):")
        for g in rel["known_gaps_status"]:
            marca = "ainda falha (esperado)" if not (g["intent_ok"] and g["cliente_ok"]) else "⚠️ PASSOU — gap pode ter sido corrigido, revisar known_gap no corpus"
            print(f"  - {g['id']}: {marca} | obtido cliente_key={g['obtido']['cliente_key']!r}")

    if rel["falhas_casos_normais"]:
        print()
        print(f"Falhas em casos normais ({len(rel['falhas_casos_normais'])}):")
        for f in rel["falhas_casos_normais"]:
            print(f"  - [{f['id']}] \"{f['pergunta']}\"")
            print(f"      esperado: {f['esperado']}")
            print(f"      obtido:   {f['obtido']}")

    print("=" * 72)
    return tudo_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(BASE_DIR / "tests" / "fixtures" / "nlp_corpus.json"))
    ap.add_argument("--out", default=str(BASE_DIR / "tests" / "fixtures" / "nlp_eval_report.json"))
    ap.add_argument("--fail-under", action="store_true",
                    help="exit code 1 se alguma meta da seção 6 não for atingida (p/ CI)")
    args = ap.parse_args()

    rel = avaliar(Path(args.corpus))
    ok = imprimir_relatorio(rel)

    Path(args.out).write_text(json.dumps(rel, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRelatório completo salvo em: {args.out}")

    if args.fail_under and not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
