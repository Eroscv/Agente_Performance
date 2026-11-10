"""
agent/claude_client.py

Cliente LLM para o Agente de Performance Guará.
Primário: Google Gemini (chaves da Flora via GEMINI_API_KEY).
Fallback: Anthropic Claude (ANTHROPIC_API_KEY, se disponível).
"""

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv

from relatorio.regras import FATOR_IMPOSTO

load_dotenv()

logger = logging.getLogger(__name__)

# ── Clientes LLM (lazy init) ─────────────────────────────────────────────────
_gemini_client = None
_anthropic_client = None


def _get_gemini():
    """Retorna cliente Gemini inicializado (ou None se chave ausente)."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from google import genai
        _gemini_client = genai.Client(api_key=api_key)
        logger.info("[LLM] Cliente Gemini inicializado.")
        return _gemini_client
    except Exception as e:
        logger.warning("[LLM] Falha ao inicializar Gemini: %s", e)
        return None


def _get_anthropic():
    """Retorna cliente Anthropic inicializado (ou None se chave ausente)."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic as _ant
        _anthropic_client = _ant.Anthropic(api_key=api_key)
        return _anthropic_client
    except Exception as e:
        logger.warning("[LLM] Falha ao inicializar Anthropic: %s", e)
        return None


def _call_llm(system: str, user_message: str) -> str:
    """Gemini → Claude (fallback). Lógica única em utils/llm.py."""
    from utils.llm import gerar
    return gerar(system, user_message, max_tokens=2048)


def get_client():
    """Compatibilidade — retorna cliente Gemini ou Anthropic."""
    c = _get_gemini()
    if c:
        return c
    return _get_anthropic()

# ── Prompts por cliente — definidos nas pastas clientes/{cliente}/prompts.py ──
from clientes.source_of.prompts import SYSTEM_PROMPT          # noqa: E402
from clientes.pod.prompts import (                             # noqa: E402
    SYSTEM_PROMPT_POD,
    SYSTEM_PROMPT_POD_META,
)


# MELHORIA 9: Base de Conhecimento local — busca por similaridade com rapidfuzz
def _buscar_kb_local(pergunta: str, intent: str, top_n: int = 3) -> str:
    """
    Busca as `top_n` entradas mais similares na tabela local kb_cache (campanhas.db).
    Usa rapidfuzz para comparar perguntas. Só retorna entradas aprovadas.
    Retorna string formatada para injetar no prompt ou string vazia.
    """
    try:
        import sqlite3 as _sq
        from pathlib import Path as _P
        from rapidfuzz import process as _fuzz_proc, fuzz as _fuzz

        _db = _P(__file__).parent.parent / "data" / "campanhas.db"
        if not _db.exists():
            return ""

        conn = _sq.connect(str(_db), check_same_thread=False)
        conn.row_factory = _sq.Row

        # Busca entradas aprovadas da mesma intent (ou qualquer se não houver suficientes)
        rows = conn.execute("""
            SELECT pergunta, resposta, intent FROM kb_cache
            WHERE aprovado = 1
            ORDER BY criado_em DESC
            LIMIT 100
        """).fetchall()
        conn.close()

        if not rows:
            return ""

        # Filtra por intent primeiro
        intent_rows = [r for r in rows if r["intent"] == intent]
        pool = intent_rows if len(intent_rows) >= top_n else list(rows)

        perguntas = [r["pergunta"] for r in pool]
        if not perguntas:
            return ""

        # Similaridade com rapidfuzz
        matches = _fuzz_proc.extract(
            pergunta,
            perguntas,
            scorer=_fuzz.token_sort_ratio,
            limit=top_n,
            score_cutoff=40,
        )
        if not matches:
            return ""

        linhas = ["[Base de Conhecimento local — exemplos aprovados similares]"]
        for match_text, score, idx in matches:
            row = pool[idx]
            linhas.append(f"  P: {row['pergunta']}")
            linhas.append(f"  R: {row['resposta'][:300]}")
            linhas.append("")

        return "\n".join(linhas).strip()
    except Exception as e:
        logger.debug("[KB local] _buscar_kb_local: %s", e)
        return ""


def _parse_structured_output(text: str) -> dict | None:
    """
    Camada 2 (Grounded Generation) — extrai JSON estruturado de uma resposta Claude.

    Ativado quando `meta[\"structured_output\"] = True` na chamada a gerar_resposta().
    O LLM é instruido a devolver JSON com rastreabilidade de cada métrica citada.

    Schema esperado:
    {
      \"headline\": str,               # título da análise
      \"periodo\": str,                # período analisado
      \"metricas_citadas\": [          # cada número com proveniência
        {\"nome\": str, \"valor\": float|str, \"fonte\": str}
      ],
      \"analise\": str,                # texto livre da análise
      \"alertas\": [str],             # ⚠️ pontos de atenção
      \"recomendacoes\": [str],        # próximos passos
      \"dado_nao_disponivel\": [str]   # campos solicitados mas ausentes nos dados
    }

    Retorna dict se parse OK, None se o LLM não devolveu JSON válido.
    """
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if not json_match:
        return None
    try:
        return json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return None


_STRUCTURED_OUTPUT_SUFFIX = """

[MODO STRUCTURED OUTPUT — CAMADA 2]
Retorne APENAS um JSON válido com o schema abaixo. Sem markdown, sem texto antes ou depois.
{
  \"headline\": \"<título da análise>\",
  \"periodo\": \"<período analisado>\",
  \"metricas_citadas\": [
    {\"nome\": \"<campo>\", \"valor\": <valor>, \"fonte\": \"<dados[N].campo>\"}
  ],
  \"analise\": \"<análise completa em texto livre, formatada para Slack>\",
  \"alertas\": [\"<alerta 1>\", \"<alerta 2>\"],
  \"recomendacoes\": [\"<ação 1>\", \"<ação 2>\"],
  \"dado_nao_disponivel\": [\"<campo solicitado mas ausente nos dados>\"]
}
Regra: cada valor em metricas_citadas deve ser rastreável aos dados fornecidos.
"""


def gerar_resposta(
    pergunta:    str,
    dados:       Any,
    intent:      str,
    meta:        dict,
    dados_extra: dict = None,
    contexto:    str  = "",
    usar_kb:     bool = True,
    delta:       dict = None,  # item 2: comparativo vs período anterior
    **kwargs,
) -> str:
    """
    Gera resposta conversacional com Claude a partir dos dados das queries.

    Args:
        pergunta:    Pergunta original do usuário
        dados:       Resultado da query principal (list[dict] ou dict)
        intent:      Intenção principal detectada
        meta:        Metadados da consulta (datas, campanha, etc.)
        dados_extra: Resultados de intenções adicionais (múltiplas intenções)
        contexto:    Histórico da thread para perguntas de acompanhamento

    Returns:
        Texto formatado para Slack
    """
    # Monta contexto dos dados
    dados_str   = _formatar_dados(dados, intent)
    periodo_str = _formatar_periodo(meta)

    # Dados extras de múltiplas intenções
    extras_str = ""
    if dados_extra:
        partes = []
        for intent_extra, dados_e in dados_extra.items():
            partes.append(f"\n[Dados adicionais — {intent_extra}]:\n{_formatar_dados(dados_e, intent_extra)}")
        extras_str = "".join(partes)

    # Contexto de thread anterior
    contexto_str = f"\n{contexto}\n" if contexto else ""

    # Aviso de ajuste automático de período (NLP)
    periodo_nota = meta.get("periodo_nota")
    periodo_nota_str = (
        f"\n\n⚠️ *Nota sobre o período:* {periodo_nota}"
        if periodo_nota else ""
    )

    # Item 2: Delta vs período anterior
    delta_str = ""
    _delta = delta or {}
    if _delta:
        _SETAS = {True: "↑", False: "↓", None: "↔"}
        partes_delta = []
        for metrica, (atual, anterior, pct) in _delta.items():
            if pct is None: continue
            seta = "↑" if pct > 0 else ("↓" if pct < 0 else "↔")
            partes_delta.append(f"{metrica}: {pct:+.1f}% {seta} (ant. {anterior:.2f})")
        if partes_delta:
            delta_str = "\n\n[Comparativo vs período anterior]\n  " + " | ".join(partes_delta)

    # Memória de longo prazo — injeta exemplos aprovados do Notion KB
    # (só para Source OF, KB não tem entradas POD ainda)
    kb_contexto = ""
    cliente_key = meta.get("cliente", "source_of")
    plataforma_meta = meta.get("plataforma")  # None = sem filtro (Meta + Google juntos)
    is_pod = cliente_key == "pod_kombucha"
    is_pod_meta = is_pod and plataforma_meta == "meta_ads"

    if usar_kb and not is_pod:
        try:
            from notion.knowledge_base import buscar_contexto, formatar_contexto_para_prompt
            campanha = meta.get("campanha")
            entradas = buscar_contexto(intent=intent, campanha=campanha)
            if entradas:
                kb_contexto = formatar_contexto_para_prompt(entradas)
                logger.debug("KB: injetando %d entradas no prompt", len(entradas))
        except Exception as e:
            logger.debug("KB: falha ao buscar contexto (não crítico): %s", e)

    # MELHORIA 9: Complementa com KB local (SQLite campanhas.db) — busca os 3 mais similares
    kb_local_contexto = ""
    try:
        kb_local_contexto = _buscar_kb_local(pergunta, intent, top_n=3)
    except Exception as e:
        logger.debug("KB local: falha ao buscar (não crítico): %s", e)

    if kb_local_contexto:
        kb_contexto = (kb_contexto + "\n\n" + kb_local_contexto).strip()

    # Dados Shopify reais (quando sincronizados)
    shopify_str = ""
    _shopify = kwargs.get("shopify") or {}
    if _shopify.get("sincronizado"):
        roas_r  = _shopify.get("roas_real")
        rec_s   = _shopify.get("receita_total")
        novos_s = _shopify.get("novos_clientes")
        ticket_s= _shopify.get("ticket_medio")
        cac_s   = _shopify.get("cac")
        recomp  = _shopify.get("taxa_recompra_pct")
        linhas_shopify = []
        if rec_s   is not None: linhas_shopify.append(f"  Receita Shopify Real: R$ {rec_s:,.2f}")
        if roas_r  is not None: linhas_shopify.append(f"  ROAS Real (Shopify÷Meta ajustado): {roas_r:.2f}x")
        if novos_s is not None: linhas_shopify.append(f"  Novos Clientes: {novos_s}")
        if ticket_s is not None: linhas_shopify.append(f"  Ticket Médio: R$ {ticket_s:.2f}")
        if cac_s   is not None: linhas_shopify.append(f"  CAC: R$ {cac_s:.2f}")
        if recomp  is not None: linhas_shopify.append(f"  Taxa de Recompra: {recomp:.1f}%")
        if linhas_shopify:
            shopify_str = (
                "\n\n[Dados Shopify reais — receita confirmada, não estimada pelo Meta]\n"
                + "\n".join(linhas_shopify)
                + "\n  ⚠️ Use o ROAS Real (Shopify) como referência principal, não o ROAS do Meta."
            )

    # MELHORIA 3: Montar linha âncora para guiar o Claude
    try:
        cliente_label = "POD Kombucha" if is_pod else "Source OF"
        if is_pod_meta:
            plataforma_label = "Meta Ads"
        elif is_pod:
            plataforma_label = "Google Ads"
        elif plataforma_meta == "google_ads":
            plataforma_label = "Google Ads"
        elif plataforma_meta == "meta_ads":
            plataforma_label = "Meta Ads"
        elif plataforma_meta is None and not is_pod:
            # Source OF sem filtro = ambas as plataformas
            plataforma_label = "Meta Ads + Google Ads"
        else:
            plataforma_label = "Meta Ads"
        if kwargs.get("shopify") and kwargs["shopify"].get("sincronizado"):
            plataforma_label += " + Shopify"
        periodo_limpo = (
            periodo_str
            .replace("Período filtrado: ", "")
            .replace("A partir de: ", "")
            .replace("Até: ", "")
            .replace("Período: todos os dados disponíveis", "todos os dados")
        )
        ancora_str = (
            f"\n\n[LINHA ÂNCORA OBRIGATÓRIA — use exatamente este formato na primeira linha da resposta]\n"
            f"📅 _Analisando: {periodo_limpo} · {cliente_label} · {plataforma_label}_"
        )
    except Exception as _ancora_err:
        logger.debug("[claude_client] ancora_str: %s", _ancora_err)
        ancora_str = ""

    # ── Grounding rules (Camada 1) ───────────────────────────────────────────────
    # Injetadas sempre — eliminam alucinação numérica sem custo extra de tokens
    # Confidence / Evidence
    _confidence_str = ""
    _cr = meta.get("confidence")
    if _cr:
        from agent.confidence import ConfidenceResult as _CRT
        _conf_obj = _CRT(
            level=_cr["level"],
            score=_cr["score"],
            reason=_cr.get("reason"),
            evidence=_cr.get("evidence", []),
            warnings=_cr.get("warnings", []),
        )
        _confidence_str = f"\n\n{_conf_obj.para_prompt()}"

    # Resultados de validação de sanidade (ValidationAgent)
    _validacao_str = ""
    _validacao = meta.get("validacao") or ""
    _validacao_extra = meta.get("validacao_extra") or {}
    if _validacao:
        _validacao_str = f"\n\n{_validacao}"
    if _validacao_extra.get("roas_ajustado_imposto"):
        _validacao_str += (
            f"\n\n[ROAS Recalculado com Imposto Meta ({FATOR_IMPOSTO}x)]\n"
            f"  Investimento ajustado: R$ {_validacao_extra['investimento_ajustado']:,.2f}\n"
            f"  ROAS real estimado: {_validacao_extra['roas_ajustado_imposto']:.2f}x\n"
            f"  (Use este valor quando ROAS Shopify não estiver disponível)"
        )

    _grounding_rules = (
        "\n\n[REGRAS DE GROUNDING — OBRIGATÓRIAS]\n"
        "1. Cite APENAS valores que aparecem literalmente nos dados acima.\n"
        "2. NUNCA estime, arredonde de forma inventada ou complete com conhecimento geral.\n"
        "3. Se um dado não está disponível nos dados fornecidos, diga explicitamente: \"dado não disponível\".\n"
        "4. Cada número na resposta deve ser rastreável a um campo nos dados acima.\n"
        "5. Variações percentuais: calcule a partir dos valores fornecidos — não invente tendências."
    )

    user_message = f"""Pergunta do usuário: {pergunta}
{contexto_str}
Intenção detectada: {intent}
{periodo_str}

Dados retornados do banco:
{dados_str}{extras_str}{delta_str}{shopify_str}{_validacao_str}{_confidence_str}{ancora_str}{_grounding_rules}

Responda a pergunta do usuário com base nesses dados. Seja analítico e direto.{periodo_nota_str}"""
    if shopify_str:
        user_message += "\n\nPRIORIDADE: Se disponível, use o ROAS Real Shopify nas análises. Destaque a diferença entre ROAS Meta vs ROAS Real quando relevante."

    # Escolhe system prompt pelo cliente e plataforma
    if is_pod_meta:
        system = SYSTEM_PROMPT_POD_META
    elif is_pod:
        system = SYSTEM_PROMPT_POD
    else:
        system = SYSTEM_PROMPT + ("\n\n" + kb_contexto if kb_contexto else "")

    # Camada 2: structured output (ativado via meta["structured_output"] = True)
    _use_structured = bool(meta.get("structured_output"))
    if _use_structured:
        user_message += _STRUCTURED_OUTPUT_SUFFIX
        logger.info("[gerar_resposta] Modo structured output ativado")

    try:
        raw_text = _call_llm(system, user_message)
        texto_final = raw_text

        # Camada 2: tenta extrair JSON estruturado se ativado
        if _use_structured:
            parsed = _parse_structured_output(raw_text)
            if parsed:
                # Retorna o campo analise (texto Slack) com metricas_citadas como log
                logger.info(
                    "[gerar_resposta] Structured output OK — %d métricas citadas, %d alertas",
                    len(parsed.get("metricas_citadas", [])),
                    len(parsed.get("alertas", [])),
                )
                if parsed.get("dado_nao_disponivel"):
                    logger.warning(
                        "[gerar_resposta] Dados não disponíveis reportados: %s",
                        parsed["dado_nao_disponivel"],
                    )
                # Usa o campo analise (texto Slack); o dict fica disponível para uso futuro
                texto_final = parsed.get("analise", raw_text)
            else:
                logger.warning("[gerar_resposta] Structured output: parse falhou, usando texto bruto")

        # ── Guards pós-geração: fact-check + compliance (nunca altera o texto) ──
        try:
            from agent.compliance_guard import verificar_compliance
            from agent.confidence import ConfidenceResult as _CRT2
            from agent.confidence import recalcular_pos_guards
            from agent.hallucination_guard import verificar_alucinacao

            hg_tipos  = verificar_alucinacao(texto_final, dados, meta)
            cg_regras = verificar_compliance(texto_final)

            if hg_tipos or cg_regras:
                _cr_pre = meta.get("confidence") or {}
                cr_pre = _CRT2(
                    level=_cr_pre.get("level", "high"),
                    score=_cr_pre.get("score", 1.0),
                    reason=_cr_pre.get("reason"),
                    evidence=_cr_pre.get("evidence", []),
                    warnings=_cr_pre.get("warnings", []),
                )
                cr_pos = recalcular_pos_guards(cr_pre, hg_tipos, cg_regras)
                meta["confidence_pos_guards"] = cr_pos.to_dict()

                log_fn = logger.warning if cr_pos.level == "low" else logger.info
                log_fn(
                    "[gerar_resposta] Guards pós-geração: hg=%s cg=%s → confiança pós-guards=%s (score=%.2f) intent=%s",
                    hg_tipos, cg_regras, cr_pos.level, cr_pos.score, intent,
                )
        except Exception as e:
            logger.debug("[gerar_resposta] Guards pós-geração falharam (não crítico): %s", e)

        return texto_final

    except Exception as e:
        logger.error("Erro no LLM: %s", e)
        return f"⚠️ Erro ao gerar análise: {e}"


# ── Colunas numéricas conhecidas para sumário estatístico ─────────────────────
_NUMERIC_COLS = {
    "investimento", "valor_compras", "compras", "impressoes", "cliques_link",
    "cliques_todos", "alcance", "frequencia", "cpm", "ctr_link", "cpc_link",
    "cpa", "roas_compras", "roas", "video_3s", "thruplays", "reproducoes_video",
    "visualizacoes_landing", "adicoes_carrinho", "checkout_iniciado",
    "compras_app", "compras_site", "conversas_iniciadas",
    # Google Ads — aliases nativos (antes de mapear para campanhas_unificada)
    "conversoes", "valor_conversao", "cliques", "ctr", "cpc_medio",
    # aliases de SQL dinâmico
    "roas_atual", "roas_anterior", "delta_roas", "delta_roas_pct",
    "investimento_atual", "investimento_anterior", "delta_investimento",
    "delta_investimento_pct", "total_compras", "total_cliques",
    "investimento_total", "valor_compras_total", "ctr_percentual",
}


def _sumario_lista(rows: list[dict]) -> str:
    """
    Para list[dict] produz:
      1. sumário estatístico (totais/médias das colunas numéricas)
      2. JSON compacto das primeiras 30 linhas
    Evita serializar centenas de linhas como texto bruto.
    """
    if not rows:
        return "Nenhum resultado encontrado."

    amostra = rows[:30]
    cols = list(rows[0].keys()) if rows else []
    num_cols = [c for c in cols if c in _NUMERIC_COLS or
                any(rows[0].get(c) is not None and isinstance(rows[0].get(c), (int, float))
                    for _ in [None])]

    # Sumário: total e média das colunas numéricas (se útil)
    sumario_parts = []
    for col in num_cols:
        vals = [r[col] for r in rows if r.get(col) is not None and isinstance(r.get(col), (int, float))]
        if not vals:
            continue
        total = sum(vals)
        media = total / len(vals)
        # ROAS e deltas: média faz mais sentido que total
        if any(x in col for x in ("roas", "ctr", "frequencia", "cpm", "cpc", "cpa", "delta_pct", "pct")):
            sumario_parts.append(f"{col}: média={media:.2f}")
        else:
            sumario_parts.append(f"{col}: total={total:.2f}")

    linhas = []
    n_total = len(rows)
    n_exibido = len(amostra)
    linhas.append(f"Total de linhas: {n_total}" + (f" (exibindo primeiras {n_exibido})" if n_total > n_exibido else ""))
    if sumario_parts:
        linhas.append("Sumário: " + " | ".join(sumario_parts))
    linhas.append("")

    # JSON compacto — uma linha por registro
    for i, row in enumerate(amostra, 1):
        # Omite valores None para reduzir ruído
        compacto = {k: v for k, v in row.items() if v is not None}
        linhas.append(f"  {i}. {json.dumps(compacto, ensure_ascii=False)}")

    if n_total > n_exibido:
        linhas.append(f"  ... e mais {n_total - n_exibido} registros")

    return "\n".join(linhas)


def _formatar_dados(dados: Any, intent: str) -> str:
    """
    Serializa dados para JSON estruturado compacto legível pelo LLM.
    Substitui serialização k=v por JSON real + sumário estatístico.
    """
    if dados is None:
        return "Nenhum dado encontrado."

    if isinstance(dados, dict):
        if not dados:
            return "Nenhum dado encontrado."

        breakdown = dados.get("breakdown_diario")
        # Campos principais como JSON compacto (sem breakdown — será separado)
        principais = {k: v for k, v in dados.items() if k != "breakdown_diario" and v is not None}
        resultado = json.dumps(principais, ensure_ascii=False, indent=2)

        # Tabela de breakdown diário (mais legível como tabela que como JSON)
        if breakdown:
            from datetime import date as _date
            _DIAS_PT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
            resultado += "\n\n[Evolução Diária da Semana]"
            resultado += "\n  Dia              | Invest.    | Receita    | ROAS  | Conv. | CTR%"
            resultado += "\n  -----------------|------------|------------|-------|-------|-----"
            for d in breakdown:
                dia_str = d.get("dia", "?")[:10]
                try:
                    _d = _date.fromisoformat(dia_str)
                    nome_dia = _DIAS_PT[_d.weekday()]
                    dia_label = f"{nome_dia} {_d.strftime('%d/%m')}"
                except Exception:
                    dia_label = dia_str
                invest  = d.get("invest") or 0
                receita = d.get("receita") or 0
                roas    = d.get("roas")
                conv    = d.get("conversoes") or 0
                ctr     = d.get("ctr") or 0
                roas_s  = f"{roas:.2f}x" if roas else "—"
                resultado += (
                    f"\n  {dia_label:<16} | R$ {invest:>8.2f} | R$ {receita:>8.2f} "
                    f"| {roas_s:>5} | {conv:>5.1f} | {ctr:>4.2f}%"
                )
        return resultado

    if isinstance(dados, list):
        return _sumario_lista(dados)

    return str(dados)


def _formatar_periodo(meta: dict) -> str:
    from datetime import date as _date
    _DIAS_PT = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

    def _fmt(d_str: str) -> str:
        """Formata data com nome do dia: 'Domingo 19/07'."""
        try:
            d = _date.fromisoformat(d_str)
            return f"{_DIAS_PT[d.weekday()]} {d.strftime('%d/%m/%Y')}"
        except Exception:
            return d_str

    inicio = meta.get("data_inicio")
    fim    = meta.get("data_fim")
    if inicio and fim:
        if inicio == fim:
            return f"Período filtrado: {_fmt(inicio)}"
        return f"Período filtrado: {_fmt(inicio)} a {_fmt(fim)}"
    if inicio:
        return f"A partir de: {_fmt(inicio)}"
    if fim:
        return f"Até: {_fmt(fim)}"
    return "Período: todos os dados disponíveis"
