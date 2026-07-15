#!/usr/bin/env python3
"""
scripts/gerar_readme_consultas.py

Gera CONSULTAS.md com histórico e estatísticas de todas as consultas realizadas.
Executado automaticamente todo dia via cron — faz commit e push no GitHub.

Seções geradas:
  1. Resumo geral (totais históricos + últimos 30 dias)
  2. Consultas mais recentes (últimas 50)
  3. Top intenções usadas (all time)
  4. Top campanhas consultadas (all time)
  5. Métricas de performance do agente (latência, cache, erros)
  6. Histórico diário dos últimos 30 dias
  7. Tabela de todas as consultas (paginada por dia, mais recente primeiro)
"""

import sys
import time
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Garante imports do projeto
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

LOG_DB   = BASE_DIR / "data" / "logs.db"
OUT_FILE = BASE_DIR / "CONSULTAS.md"

# ── helpers ───────────────────────────────────────────────────────────────────

def fmt_ts(ts: float) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

def fmt_date(ts: float) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m/%Y")

def fmt_ms(ms) -> str:
    if ms is None:
        return "—"
    return f"{int(ms)}ms"

def clean_pergunta(p: str) -> str:
    """Remove mentions Slack e formata para markdown."""
    if not p:
        return "—"
    import re
    p = re.sub(r"<@[A-Z0-9]+>", "", p).strip()
    p = re.sub(r"\s+", " ", p)
    # Escapa pipes e quebras de linha para tabela markdown
    p = p.replace("|", "\\|").replace("\n", " ")
    return p[:120] + ("…" if len(p) > 120 else "")

def intent_emoji(intent: str) -> str:
    mapa = {
        "top_roas":               "🏆",
        "top_compras":            "🛒",
        "top_receita":            "💰",
        "top_ctr":                "👆",
        "top_cpm":                "📺",
        "top_alcance":            "👥",
        "top_cliques":            "🖱️",
        "ranking_metrica":        "📊",
        "resumo_geral":           "📋",
        "resumo_executivo":       "📌",
        "performance_campanha":   "📈",
        "periodo":                "📅",
        "evolucao_diaria":        "📉",
        "historico_mensal":       "🗓️",
        "listar_campanhas":       "📝",
        "funil":                  "🔽",
        "funil_detalhado":        "🔬",
        "alertas_roas":           "🚨",
        "sem_conversao":          "❌",
        "campanhas_inativas":     "⏸️",
        "variacao_roas":          "〰️",
        "saturacao_frequencia":   "🔁",
        "eficiencia_criativo":    "🎨",
        "distribuicao_investimento": "🥧",
        "metricas_video":         "🎬",
        "metricas_engajamento":   "❤️",
        "metricas_mensageria":    "💬",
        "comparar":               "⚖️",
        "comparar_periodos":      "📆",
        "comparar_wow":           "📊",
        "comparar_mom":           "📊",
        "comparar_antes_depois":  "⏩",
    }
    return mapa.get(intent or "", "❓")


def get_conn() -> sqlite3.Connection:
    if not LOG_DB.exists():
        raise FileNotFoundError(f"Log DB não encontrado: {LOG_DB}")
    conn = sqlite3.connect(str(LOG_DB))
    conn.row_factory = sqlite3.Row
    return conn


# ── seções do README ──────────────────────────────────────────────────────────

def secao_resumo(conn: sqlite3.Connection) -> str:
    now   = time.time()
    d30   = now - 30 * 86400
    d7    = now - 7  * 86400
    d1    = now - 1  * 86400

    total = conn.execute("SELECT COUNT(*) FROM conversation_logs").fetchone()[0]
    first = conn.execute("SELECT MIN(ts) FROM conversation_logs").fetchone()[0]
    last  = conn.execute("SELECT MAX(ts) FROM conversation_logs").fetchone()[0]

    def stats(since):
        r = conn.execute("""
            SELECT
                COUNT(*)                                        AS total,
                COUNT(DISTINCT user_id)                         AS usuarios,
                ROUND(AVG(latencia_ms), 0)                      AS lat_media,
                SUM(cache_hit)                                  AS cache_hits,
                COUNT(CASE WHEN erro IS NOT NULL THEN 1 END)    AS erros,
                COUNT(CASE WHEN cache_hit = 0 AND erro IS NULL THEN 1 END) AS ok
            FROM conversation_logs WHERE ts >= ?
        """, (since,)).fetchone()
        return dict(r) if r else {}

    s30 = stats(d30)
    s7  = stats(d7)
    s1  = stats(d1)

    gerado_em = datetime.now(tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    lines = [
        "# 📊 CONSULTAS.md — Histórico do Agente de Performance Guará",
        "",
        f"> Atualizado automaticamente · {gerado_em}",
        "",
        "---",
        "",
        "## 🔢 Resumo Geral",
        "",
        f"| Métrica | Valor |",
        f"|---------|-------|",
        f"| Total de consultas (histórico) | **{total}** |",
        f"| Primeira consulta | {fmt_ts(first)} |",
        f"| Última consulta   | {fmt_ts(last)} |",
        "",
        "### Últimos períodos",
        "",
        "| Período | Consultas | Usuários | Latência média | Cache hits | Erros |",
        "|---------|-----------|----------|----------------|------------|-------|",
        f"| Últimas 24h   | {s1.get('total',0)}  | {s1.get('usuarios',0)}  | {fmt_ms(s1.get('lat_media'))} | {s1.get('cache_hits') or 0} | {s1.get('erros',0)} |",
        f"| Últimos 7 dias | {s7.get('total',0)} | {s7.get('usuarios',0)} | {fmt_ms(s7.get('lat_media'))} | {s7.get('cache_hits') or 0} | {s7.get('erros',0)} |",
        f"| Últimos 30 dias | {s30.get('total',0)} | {s30.get('usuarios',0)} | {fmt_ms(s30.get('lat_media'))} | {s30.get('cache_hits') or 0} | {s30.get('erros',0)} |",
        "",
    ]
    return "\n".join(lines)


def secao_top_intents(conn: sqlite3.Connection) -> str:
    rows = conn.execute("""
        SELECT intent, COUNT(*) AS n,
               ROUND(AVG(latencia_ms), 0) AS lat_media,
               SUM(cache_hit) AS cache_hits
        FROM conversation_logs
        WHERE intent IS NOT NULL
        GROUP BY intent ORDER BY n DESC
    """).fetchall()

    if not rows:
        return "## 🎯 Top Intenções\n\n_Sem dados_\n"

    lines = [
        "## 🎯 Top Intenções (histórico completo)",
        "",
        "| # | Intent | Tipo | Consultas | Lat. média | Cache hits |",
        "|---|--------|------|-----------|------------|------------|",
    ]
    for i, r in enumerate(rows, 1):
        intent = r["intent"] or "—"
        emoji  = intent_emoji(intent)
        lines.append(
            f"| {i} | `{intent}` | {emoji} | {r['n']} | {fmt_ms(r['lat_media'])} | {r['cache_hits'] or 0} |"
        )
    lines.append("")
    return "\n".join(lines)


def secao_top_campanhas(conn: sqlite3.Connection) -> str:
    rows = conn.execute("""
        SELECT campanha, COUNT(*) AS n,
               COUNT(DISTINCT user_id) AS usuarios
        FROM conversation_logs
        WHERE campanha IS NOT NULL AND campanha != ''
        GROUP BY campanha ORDER BY n DESC LIMIT 20
    """).fetchall()

    if not rows:
        return "## 🏷️ Campanhas Mais Consultadas\n\n_Sem dados_\n"

    lines = [
        "## 🏷️ Campanhas Mais Consultadas (top 20)",
        "",
        "| # | Campanha | Consultas | Usuários únicos |",
        "|---|----------|-----------|-----------------|",
    ]
    for i, r in enumerate(rows, 1):
        camp = (r["campanha"] or "—")[:60]
        lines.append(f"| {i} | `{camp}` | {r['n']} | {r['usuarios']} |")
    lines.append("")
    return "\n".join(lines)


def secao_historico_diario(conn: sqlite3.Connection) -> str:
    rows = conn.execute("""
        SELECT
            DATE(ts, 'unixepoch') AS dia,
            COUNT(*) AS total,
            COUNT(CASE WHEN erro IS NOT NULL THEN 1 END) AS erros,
            SUM(cache_hit) AS cache_hits,
            ROUND(AVG(latencia_ms), 0) AS lat_media,
            COUNT(DISTINCT intent) AS intents_distintas
        FROM conversation_logs
        GROUP BY dia
        ORDER BY dia DESC
        LIMIT 60
    """).fetchall()

    if not rows:
        return "## 📅 Histórico Diário\n\n_Sem dados_\n"

    lines = [
        "## 📅 Histórico Diário (últimos 60 dias)",
        "",
        "| Data | Consultas | Erros | Cache hits | Lat. média | Intents distintas |",
        "|------|-----------|-------|------------|------------|-------------------|",
    ]
    for r in rows:
        dia = r["dia"] or "—"
        # Reformata data
        try:
            d = datetime.strptime(dia, "%Y-%m-%d")
            dia = d.strftime("%d/%m/%Y")
        except Exception:
            pass
        lines.append(
            f"| {dia} | {r['total']} | {r['erros'] or 0} | {r['cache_hits'] or 0} | {fmt_ms(r['lat_media'])} | {r['intents_distintas']} |"
        )
    lines.append("")
    return "\n".join(lines)


def secao_ultimas_consultas(conn: sqlite3.Connection, limit: int = 100) -> str:
    rows = conn.execute("""
        SELECT ts, user_id, pergunta, intent, campanha,
               data_inicio, data_fim, latencia_ms, cache_hit, erro
        FROM conversation_logs
        ORDER BY ts DESC
        LIMIT ?
    """, (limit,)).fetchall()

    if not rows:
        return f"## 🕐 Últimas {limit} Consultas\n\n_Sem dados_\n"

    lines = [
        f"## 🕐 Últimas {limit} Consultas",
        "",
        "| Data/Hora | Usuário | Pergunta | Intent | Campanha | Período | Status |",
        "|-----------|---------|----------|--------|----------|---------|--------|",
    ]
    for r in rows:
        dt       = fmt_ts(r["ts"])
        user     = (r["user_id"] or "—")[:20]
        pergunta = clean_pergunta(r["pergunta"])
        intent   = r["intent"] or "—"
        emoji    = intent_emoji(intent)
        camp     = (r["campanha"] or "—")[:30]
        periodo  = "—"
        if r["data_inicio"] and r["data_fim"]:
            try:
                di = datetime.strptime(r["data_inicio"], "%Y-%m-%d").strftime("%d/%m")
                df = datetime.strptime(r["data_fim"],    "%Y-%m-%d").strftime("%d/%m/%y")
                periodo = f"{di}→{df}"
            except Exception:
                periodo = f"{r['data_inicio']}→{r['data_fim']}"
        cache    = "💾 cache" if r["cache_hit"] else ""
        erro_str = "❌ erro"  if r["erro"]      else "✅"
        status   = erro_str + (" " + cache if cache else "")
        lines.append(
            f"| {dt} | {user} | {pergunta} | {emoji} `{intent}` | {camp} | {periodo} | {status} |"
        )
    lines.append("")
    return "\n".join(lines)


def secao_todas_consultas(conn: sqlite3.Connection) -> str:
    """Tabela completa agrupada por dia, mais recente primeiro."""
    dias = conn.execute("""
        SELECT DISTINCT DATE(ts, 'unixepoch') AS dia
        FROM conversation_logs
        ORDER BY dia DESC
    """).fetchall()

    if not dias:
        return "## 📋 Todas as Consultas\n\n_Sem dados_\n"

    lines = [
        "## 📋 Todas as Consultas (agrupadas por dia)",
        "",
    ]

    for dia_row in dias:
        dia = dia_row["dia"]
        try:
            d   = datetime.strptime(dia, "%Y-%m-%d")
            dia_fmt = d.strftime("%d/%m/%Y (%A)").replace(
                "Monday","Segunda").replace("Tuesday","Terça").replace(
                "Wednesday","Quarta").replace("Thursday","Quinta").replace(
                "Friday","Sexta").replace("Saturday","Sábado").replace("Sunday","Domingo")
        except Exception:
            dia_fmt = dia

        rows = conn.execute("""
            SELECT ts, user_id, pergunta, intent, campanha,
                   data_inicio, data_fim, latencia_ms, cache_hit, erro
            FROM conversation_logs
            WHERE DATE(ts, 'unixepoch') = ?
            ORDER BY ts DESC
        """, (dia,)).fetchall()

        total_dia = len(rows)
        erros_dia = sum(1 for r in rows if r["erro"])

        lines += [
            f"### 📅 {dia_fmt} — {total_dia} consulta(s){' · ⚠️ ' + str(erros_dia) + ' erro(s)' if erros_dia else ''}",
            "",
            "| Hora (UTC) | Usuário | Pergunta | Intent | Campanha | Período | ms | Status |",
            "|------------|---------|----------|--------|----------|---------|-----|--------|",
        ]

        for r in rows:
            hora     = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%H:%M:%S")
            user     = (r["user_id"] or "—")[:15]
            pergunta = clean_pergunta(r["pergunta"])
            intent   = r["intent"] or "—"
            emoji    = intent_emoji(intent)
            camp     = (r["campanha"] or "—")[:25]
            periodo  = "—"
            if r["data_inicio"] and r["data_fim"]:
                try:
                    di = datetime.strptime(r["data_inicio"], "%Y-%m-%d").strftime("%d/%m")
                    df = datetime.strptime(r["data_fim"],    "%Y-%m-%d").strftime("%d/%m/%y")
                    periodo = f"{di}→{df}"
                except Exception:
                    periodo = f"{r['data_inicio'][:5]}→{r['data_fim'][5:]}"
            ms_str   = str(r["latencia_ms"]) if r["latencia_ms"] else "—"
            status   = ("❌" if r["erro"] else "✅") + (" 💾" if r["cache_hit"] else "")
            lines.append(
                f"| {hora} | {user} | {pergunta} | {emoji} `{intent}` | {camp} | {periodo} | {ms_str} | {status} |"
            )

        lines.append("")

    return "\n".join(lines)


def gerar_readme() -> str:
    conn = get_conn()
    parts = [
        secao_resumo(conn),
        secao_top_intents(conn),
        secao_top_campanhas(conn),
        secao_historico_diario(conn),
        secao_ultimas_consultas(conn, 50),
        secao_todas_consultas(conn),
        "---",
        f"_Gerado automaticamente por `scripts/gerar_readme_consultas.py` · {datetime.now(tz=timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}_",
        "",
    ]
    return "\n".join(parts)


if __name__ == "__main__":
    print("[gerar_readme] Iniciando geração de CONSULTAS.md...")
    content = gerar_readme()
    OUT_FILE.write_text(content, encoding="utf-8")
    linhas = len(content.splitlines())
    print(f"[gerar_readme] ✅ CONSULTAS.md gerado: {linhas} linhas → {OUT_FILE}")
