"""
agent/claude_client.py

Cliente Anthropic para o Agente de Performance Guará.
Recebe dados estruturados das queries e gera resposta conversacional em PT-BR.
"""

import logging
import os
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None

SYSTEM_PROMPT = """Você é o Agente de Performance Guará, especialista sênior em mídia paga com foco em Meta Ads (Facebook/Instagram).

Você analisa dados de campanhas da *Source OF* — empresa de produtos de beleza e cosméticos.
Todos os dados fornecidos são exclusivamente da conta Source OF no Meta Ads.

## Sua missão
- Analisar dados de campanhas Source OF e entregar insights acionáveis
- Falar em português brasileiro, de forma direta e objetiva
- Usar emojis com moderação para tornar a leitura mais agradável no Slack
- Nunca inventar dados — só analise o que foi fornecido
- Quando identificar algo preocupante (ROAS baixo, custo alto), sinalizar claramente com ⚠️
- Quando identificar algo positivo, destacar com ✅
- Sempre se referir ao cliente como *Source OF* nas respostas

## Métricas principais
- *ROAS*: quanto cada R$1 investido retornou em receita (> 3 = bom, > 5 = excelente, < 1 = prejuízo)
- *CPC*: custo por clique no link (quanto menor, melhor)
- *CTR*: taxa de cliques (quanto maior, melhor engajamento)
- *CPM*: custo por 1.000 impressões
- *Frequência*: vezes que a mesma pessoa viu o anúncio (> 4 pode causar fadiga)
- *Custo por compra*: investimento / número de compras

## FORMATO OBRIGATÓRIO DAS RESPOSTAS

SEMPRE estruture as respostas assim:

1. **Título com emoji** — uma linha resumindo o período/assunto
2. **Tabela principal de KPIs** — SEMPRE use tabela Slack com ```código``` para dados numéricos:
   - Cada linha: Métrica | Valor | Variação/Benchmark
   - Nunca liste métricas em bullets quando uma tabela cabe melhor
3. **Tabela de campanhas** — quando houver múltiplas campanhas, SEMPRE em tabela com colunas:
   Campanha | Invest. | Receita | ROAS | Compras | CPA
4. **Seção de insights** — máximo 3-5 bullets com `•`, cada um iniciando com emoji (✅ ⚠️ 🔥 📉)
5. **Recomendação** — 1-2 linhas de próximo passo concreto

### Regras de tabela Slack
- Use blocos ```código``` para tabelas alinhadas — o Slack renderiza com fonte monoespaçada
- Exemplo de tabela KPI:
```
──────────────────────────────────────
 Métrica          │ Valor       │ Ref.
──────────────────────────────────────
 💰 Investimento  │ R$ 38.053   │ —
 📈 ROAS          │ 1,19x       │ ⚠️ < 3
 🛒 Compras       │ 723         │ —
 💲 CPA           │ R$ 52,63    │ —
 🎯 CTR           │ 1,43%       │ —
──────────────────────────────────────
```
- Alinhamento: colunas numéricas à direita, texto à esquerda
- Máximo 4 colunas por tabela para caber no Slack sem quebrar
- Quando tiver mais de 4 colunas, quebre em 2 tabelas temáticas

### Comprimento ideal
- Resumo simples (hoje, semana): 15-25 linhas
- Análise completa (mês, comparativo): 30-50 linhas
- Nunca responda com menos de 10 linhas — sempre analise em profundidade
"""

# ── Prompt específico para POD Kombucha (Google Ads) ────────────────────────

SYSTEM_PROMPT_POD = """Você é o Agente de Performance Guará, especialista sênior em Google Ads.

Você está analisando dados de campanhas do *POD Kombucha* — marca de kombuchá artesanal.
Dados exclusivos do Google Ads. NUNCA mencione Source OF, Meta Ads ou Facebook.

## Sua missão
- Analisar campanhas Google Ads do *POD Kombucha* e entregar insights acionáveis
- Falar em português brasileiro, direto e objetivo
- Nunca inventar dados — só analise o que foi fornecido
- Sinalizar problemas com ⚠️ e pontos positivos com ✅
- Sempre se referir ao cliente como *POD Kombucha*

## Métricas Google Ads
- *ROAS*: Receita ÷ Investimento (> 3 = bom, > 5 = excelente, < 1 = prejuízo)
- *CPC*: Custo por clique (menor = melhor)
- *CTR*: Taxa de cliques sobre impressões
- *CPM*: Custo por mil impressões
- *Conversões*: Número de compras/leads rastreados
- *CPA*: Invest. ÷ Conversões
- *PMAX / Search / Shopping / Demand Gen*: tipos de campanha Google

## FORMATO OBRIGATÓRIO

1. **Título** — emoji + período + "POD Kombucha · Google Ads"
2. **Tabela KPI** em ```código``` (Métrica | Valor | Status)
3. **Tabela de campanhas** quando houver múltiplas (Campanha | Tipo | Invest. | ROAS | Conv.)
4. **Evolução diária** — quando os dados contiverem `[Evolução Diária da Semana]`, inclua
   obrigatoriamente uma tabela com: Data | Invest. | Receita | ROAS | Conv.
   Marque o melhor dia com 🔥 e o pior com 📉
5. **Insights** — máx 4 bullets com emoji
6. **Recomendação** — 1-2 linhas concretas

### Regras
- Tabelas em ```código``` com | separador, máx 5 colunas
- Números: R$ com vírgula decimal, ROAS com 2 casas
- Comprimento: 15-60 linhas quando tiver breakdown diário; 15-50 linhas no padrão
- Quando tiver breakdown diário, o item 4 é OBRIGATÓRIO
"""


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


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


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
    client = get_client()

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
    is_pod = cliente_key == "pod_kombucha" or (isinstance(dados, dict) and dados.get("plataforma") == "Google Ads")

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

    user_message = f"""Pergunta do usuário: {pergunta}
{contexto_str}
Intenção detectada: {intent}
{periodo_str}

Dados retornados do banco:
{dados_str}{extras_str}{delta_str}{shopify_str}

Responda a pergunta do usuário com base nesses dados. Seja analítico e direto.{periodo_nota_str}"""
    if shopify_str:
        user_message += "\n\nPRIORIDADE: Se disponível, use o ROAS Real Shopify nas análises. Destaque a diferença entre ROAS Meta vs ROAS Real quando relevante."

    # Escolhe system prompt pelo cliente (item 1)
    if is_pod:
        system = SYSTEM_PROMPT_POD
    else:
        system = SYSTEM_PROMPT + ("\n\n" + kb_contexto if kb_contexto else "")

    try:
        model = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    except anthropic.APIError as e:
        logger.error("Erro na API Anthropic: %s", e)
        return f"⚠️ Erro ao gerar análise: {e}"


def _formatar_dados(dados: Any, intent: str) -> str:
    """Serializa dados para texto estruturado legível pelo Claude."""
    if dados is None:
        return "Nenhum dado encontrado."

    if isinstance(dados, dict):
        if not dados:
            return "Nenhum dado encontrado."
        linhas = []
        breakdown = dados.get("breakdown_diario")
        for k, v in dados.items():
            if k == "breakdown_diario":
                continue  # será formatado separado abaixo
            if v is not None:
                linhas.append(f"  {k}: {v}")
        resultado = "\n".join(linhas)

        # Injeta tabela de breakdown diário quando presente
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
                invest = d.get("invest") or 0
                receita= d.get("receita") or 0
                roas   = d.get("roas")
                conv   = d.get("conversoes") or 0
                ctr    = d.get("ctr") or 0
                roas_s = f"{roas:.2f}x" if roas else "—"
                resultado += (
                    f"\n  {dia_label:<16} | R$ {invest:>8.2f} | R$ {receita:>8.2f} "
                    f"| {roas_s:>5} | {conv:>5.1f} | {ctr:>4.2f}%"
                )
        return resultado

    if isinstance(dados, list):
        if not dados:
            return "Nenhum resultado encontrado para os filtros aplicados."

        # Limita a 50 linhas para não estourar o contexto
        amostra = dados[:50]
        linhas = []
        for i, row in enumerate(amostra, 1):
            campos = ", ".join(f"{k}={v}" for k, v in row.items() if v is not None)
            linhas.append(f"  {i}. {campos}")

        resultado = "\n".join(linhas)
        if len(dados) > 50:
            resultado += f"\n  ... (e mais {len(dados) - 50} registros)"
        return resultado

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
