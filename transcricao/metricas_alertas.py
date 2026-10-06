"""
metricas_alertas.py

Métricas de SLA baseadas em tickets — não em amostra de mensagens.

PROBLEMA com "90% de urgentes em amostra de 200 mensagens":
  - Urgentes são eventos raros (~3-5 por mês)
  - Com n=5, IC 95% de uma proporção de 80% vai de 28% a 99%
  - A métrica não tem poder estatístico

SOLUÇÃO — métricas absolutas por ticket:
  1. Tempo médio de resolução por tipo (dias úteis)
  2. Taxa de resolução no período (absoluta, não amostral)
  3. Tickets em aberto há mais de N dias (SLA vencido)
  4. Tendência: novos vs fechados por semana

Uso:
  python3 metricas_alertas.py              # relatório atual
  python3 metricas_alertas.py --slack      # envia resumo no Slack
  python3 metricas_alertas.py --json       # saída JSON (para BigQuery)
"""

import argparse
import json
import math
import os
import sqlite3
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from google.cloud import bigquery as bq
from google.oauth2 import service_account as _sa

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
ENV_FILE   = BASE_DIR.parent / "agente-performance" / ".env"
DB_PATH    = BASE_DIR / "db" / "reunioes.db"
BQ_CREDS   = BASE_DIR / "credentials" / "gdrive-service-account.json"

load_dotenv(ENV_FILE)
SLACK_BOT_TOKEN     = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_ALERT_CHANNEL = os.getenv("SLACK_ALERT_CHANNEL", "performance")
BQ_PROJECT          = "guara-db"
BQ_DATASET          = "guara_performance"
BQ_TABLE_METRICAS   = "metricas_sla"

# SLA por tipo (dias corridos)
SLA_DIAS = {
    "urgente":      2,
    "sem_resposta": 5,
    "bloqueio":     3,
    "sem_acesso":   3,
    "conflito":     7,
}


# ── Cálculo de IC de Wilson (proporção com n pequeno) ─────────────────────────

def ic_wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """
    Intervalo de confiança de Wilson para proporção.
    Retorna (lower, upper) como frações 0-1.
    Correto para n pequeno (ao contrário do IC normal que falha com n<30).
    """
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


# ── Coleta de dados ───────────────────────────────────────────────────────────

def coletar_metricas(conn: sqlite3.Connection, janela_dias: int = 90) -> dict:
    desde = (date.today() - timedelta(days=janela_dias)).isoformat()
    agora_str = datetime.now(timezone.utc).isoformat()

    # ── Totais por tipo e status ──────────────────────────────────────────────
    rows = conn.execute("""
        SELECT tipo, status,
               COUNT(*) as n,
               AVG(CASE
                 WHEN status = 'resolvido' AND resolvido_em IS NOT NULL
                 THEN julianday(resolvido_em) - julianday(aberto_em)
                 ELSE NULL
               END) as avg_dias_resolucao,
               MAX(CASE
                 WHEN status = 'resolvido' AND resolvido_em IS NOT NULL
                 THEN julianday(resolvido_em) - julianday(aberto_em)
                 ELSE NULL
               END) as max_dias_resolucao
        FROM tickets_alerta
        WHERE aberto_em >= ?
        GROUP BY tipo, status
    """, (desde,)).fetchall()

    # Organiza em dict[tipo][status] = {n, avg_dias}
    por_tipo: dict[str, dict] = {}
    for r in rows:
        t = r["tipo"]
        if t not in por_tipo:
            por_tipo[t] = {"aberto": 0, "resolvido": 0, "fechado": 0,
                           "avg_dias": None, "max_dias": None}
        por_tipo[t][r["status"]] = r["n"]
        if r["avg_dias_resolucao"] is not None:
            por_tipo[t]["avg_dias"] = round(r["avg_dias_resolucao"], 1)
        if r["max_dias_resolucao"] is not None:
            por_tipo[t]["max_dias"] = round(r["max_dias_resolucao"], 1)

    # ── SLA vencido (abertos há mais de SLA_DIAS[tipo]) ───────────────────────
    sla_vencidos = []
    for tipo, sla in SLA_DIAS.items():
        limite = (date.today() - timedelta(days=sla)).isoformat()
        vencidos = conn.execute("""
            SELECT ticket_id, cliente, descricao, reuniao_data, aberto_em
            FROM tickets_alerta
            WHERE tipo = ? AND status = 'aberto'
              AND aberto_em <= ?
            ORDER BY aberto_em
        """, (tipo, limite + "T23:59:59")).fetchall()
        for v in vencidos:
            dias_aberto = (date.today() - date.fromisoformat(v["aberto_em"][:10])).days
            sla_vencidos.append({
                "ticket_id":  v["ticket_id"],
                "tipo":       tipo,
                "cliente":    v["cliente"],
                "descricao":  v["descricao"],
                "dias_aberto": dias_aberto,
                "sla_dias":   sla,
                "atraso_dias": dias_aberto - sla,
            })
    sla_vencidos.sort(key=lambda x: -x["atraso_dias"])

    # ── Taxa de resolução com IC de Wilson ────────────────────────────────────
    taxas = {}
    for tipo, dados in por_tipo.items():
        total     = dados["aberto"] + dados["resolvido"] + dados["fechado"]
        resolvidos = dados["resolvido"] + dados["fechado"]
        ic_low, ic_high = ic_wilson(resolvidos, total)
        sla       = SLA_DIAS.get(tipo, 7)
        avg       = dados["avg_dias"]

        # Aviso sobre confiabilidade estatística
        confiavel = total >= 20  # IC estreito o suficiente para ser útil
        aviso_ic  = None
        if not confiavel:
            w = ic_high - ic_low
            aviso_ic = (
                f"n={total} — IC 95% de {ic_low*100:.0f}% a {ic_high*100:.0f}% "
                f"(amplitude {w*100:.0f}pp). Métrica percentual não confiável com n<20."
            )

        taxas[tipo] = {
            "total":         total,
            "resolvidos":    resolvidos,
            "abertos":       dados["aberto"],
            "taxa_pct":      round(resolvidos / total * 100, 1) if total else None,
            "ic_low_pct":    round(ic_low * 100, 1),
            "ic_high_pct":   round(ic_high * 100, 1),
            "avg_dias":      avg,
            "sla_dias":      sla,
            "dentro_sla":    (avg is not None and avg <= sla),
            "confiavel":     confiavel,
            "aviso_ic":      aviso_ic,
        }

    # ── Tendência semanal (novos vs fechados) ─────────────────────────────────
    tendencia = conn.execute("""
        SELECT
          strftime('%Y-W%W', aberto_em) as semana,
          COUNT(*) as novos,
          SUM(CASE WHEN status IN ('resolvido','fechado') THEN 1 ELSE 0 END) as fechados
        FROM tickets_alerta
        WHERE aberto_em >= ?
        GROUP BY semana
        ORDER BY semana
    """, (desde,)).fetchall()

    return {
        "gerado_em":   agora_str,
        "janela_dias": janela_dias,
        "por_tipo":    por_tipo,
        "taxas":       taxas,
        "sla_vencidos": sla_vencidos,
        "tendencia":   [dict(r) for r in tendencia],
    }


# ── Formatação do relatório ───────────────────────────────────────────────────

EMOJI_TIPO = {
    "urgente":      "🚨",
    "sem_resposta": "🔇",
    "bloqueio":     "🚫",
    "sem_acesso":   "🔒",
    "conflito":     "⚠️",
}


def formatar_relatorio(m: dict) -> str:
    linhas = [
        f"📊 *Métricas de SLA — últimos {m['janela_dias']} dias*",
        f"_Gerado em {m['gerado_em'][:10]}_",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "*Por tipo de alerta*",
        "",
    ]

    for tipo, t in m["taxas"].items():
        emoji = EMOJI_TIPO.get(tipo, "⚠️")
        sla_icon = "✅" if t["dentro_sla"] else ("❌" if t["avg_dias"] else "⏳")

        linhas.append(f"{emoji} *{tipo.upper()}* (SLA: {t['sla_dias']}d)")
        linhas.append(f"  • Total: {t['total']} | Abertos: {t['abertos']} | Resolvidos: {t['resolvidos']}")

        if t["avg_dias"] is not None:
            linhas.append(f"  • Tempo médio resolução: {t['avg_dias']}d {sla_icon}")
        else:
            linhas.append(f"  • Tempo médio: sem dados ainda")

        if t["aviso_ic"]:
            # Mostra por que % não serve aqui
            linhas.append(f"  ⚠️ _{t['aviso_ic']}_")
        else:
            linhas.append(f"  • Taxa resolução: {t['taxa_pct']}% (IC 95%: {t['ic_low_pct']}–{t['ic_high_pct']}%)")

        linhas.append("")

    # SLA vencidos
    if m["sla_vencidos"]:
        linhas += [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🔴 *SLA vencido ({len(m['sla_vencidos'])} tickets)*",
            "",
        ]
        for v in m["sla_vencidos"][:5]:  # top 5 mais atrasados
            emoji = EMOJI_TIPO.get(v["tipo"], "⚠️")
            linhas.append(
                f"{emoji} `{v['ticket_id']}` — {v['cliente'] or '?'} — "
                f"{v['dias_aberto']}d aberto (SLA {v['sla_dias']}d, atraso {v['atraso_dias']}d)"
            )
            linhas.append(f"  _{v['descricao'][:80]}_")
        linhas.append("")

    # Tendência
    if m["tendencia"]:
        linhas += [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "*Tendência semanal (novos vs fechados)*",
            "",
        ]
        for s in m["tendencia"]:
            barra_n = "█" * min(s["novos"], 10)
            barra_f = "░" * min(s["fechados"], 10)
            linhas.append(f"  {s['semana']}: +{s['novos']} {barra_n} / -{s['fechados']} {barra_f}")
        linhas.append("")

    # Nota metodológica
    linhas += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "_📌 Nota: métricas baseadas em tickets individuais, não em amostra de mensagens._",
        "_Percentual só é exibido quando n≥20 (IC estreito o suficiente para ser útil)._",
        "_Use 'tempo médio de resolução vs SLA' como métrica primária._",
    ]

    return "\n".join(linhas)


# ── BigQuery — salva snapshot de métricas ─────────────────────────────────────

BQ_SCHEMA_METRICAS = [
    bq.SchemaField("gerado_em",       "TIMESTAMP", mode="REQUIRED"),
    bq.SchemaField("janela_dias",     "INTEGER",   mode="REQUIRED"),
    bq.SchemaField("tipo",            "STRING",    mode="REQUIRED"),
    bq.SchemaField("total",           "INTEGER",   mode="NULLABLE"),
    bq.SchemaField("abertos",         "INTEGER",   mode="NULLABLE"),
    bq.SchemaField("resolvidos",      "INTEGER",   mode="NULLABLE"),
    bq.SchemaField("avg_dias",        "FLOAT",     mode="NULLABLE"),
    bq.SchemaField("sla_dias",        "INTEGER",   mode="NULLABLE"),
    bq.SchemaField("dentro_sla",      "BOOLEAN",   mode="NULLABLE"),
    bq.SchemaField("sla_vencidos",    "INTEGER",   mode="NULLABLE"),
    bq.SchemaField("ic_low_pct",      "FLOAT",     mode="NULLABLE"),
    bq.SchemaField("ic_high_pct",     "FLOAT",     mode="NULLABLE"),
    bq.SchemaField("confiavel",       "BOOLEAN",   mode="NULLABLE"),
    bq.SchemaField("atualizado_em",   "TIMESTAMP", mode="REQUIRED"),
]


def salvar_metricas_bq(m: dict):
    try:
        creds = _sa.Credentials.from_service_account_file(
            str(BQ_CREDS), scopes=["https://www.googleapis.com/auth/bigquery"]
        )
        client = bq.Client(project=BQ_PROJECT, credentials=creds)
        dataset_ref = client.dataset(BQ_DATASET)
        table_ref   = dataset_ref.table(BQ_TABLE_METRICAS)
        try:
            table = client.get_table(table_ref)
        except Exception:
            table = bq.Table(table_ref, schema=BQ_SCHEMA_METRICAS)
            table = client.create_table(table)

        agora = datetime.now(timezone.utc).isoformat()
        rows = []
        sla_vencidos_por_tipo = {}
        for v in m["sla_vencidos"]:
            sla_vencidos_por_tipo[v["tipo"]] = sla_vencidos_por_tipo.get(v["tipo"], 0) + 1

        for tipo, t in m["taxas"].items():
            rows.append({
                "gerado_em":     m["gerado_em"],
                "janela_dias":   m["janela_dias"],
                "tipo":          tipo,
                "total":         t["total"],
                "abertos":       t["abertos"],
                "resolvidos":    t["resolvidos"],
                "avg_dias":      t["avg_dias"],
                "sla_dias":      t["sla_dias"],
                "dentro_sla":    t["dentro_sla"],
                "sla_vencidos":  sla_vencidos_por_tipo.get(tipo, 0),
                "ic_low_pct":    t["ic_low_pct"],
                "ic_high_pct":   t["ic_high_pct"],
                "confiavel":     t["confiavel"],
                "atualizado_em": agora,
            })

        errors = client.insert_rows_json(table, rows)
        if errors:
            print(f"[BQ] Erros: {errors}")
        else:
            print(f"[BQ] ✅ {len(rows)} métricas salvas em {BQ_TABLE_METRICAS}")
    except Exception as e:
        print(f"[BQ] Exceção: {e}")


# ── Slack ─────────────────────────────────────────────────────────────────────

def enviar_slack(texto: str):
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "channel": SLACK_ALERT_CHANNEL,
            "text":    "Relatório de métricas SLA",
            "blocks": [{
                "type": "section",
                "text": {"type": "mrkdwn", "text": texto},
            }],
        },
        timeout=10,
    )
    if resp.json().get("ok"):
        print("✅ Enviado no Slack")
    else:
        print(f"❌ Slack: {resp.json().get('error')}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slack",  action="store_true", help="Envia resumo no Slack")
    parser.add_argument("--json",   action="store_true", help="Saída JSON")
    parser.add_argument("--bq",     action="store_true", help="Salva snapshot no BigQuery")
    parser.add_argument("--janela", type=int, default=90, help="Janela em dias (padrão: 90)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    m = coletar_metricas(conn, janela_dias=args.janela)
    conn.close()

    if args.json:
        print(json.dumps(m, indent=2, ensure_ascii=False, default=str))
        return

    relatorio = formatar_relatorio(m)
    print(relatorio)

    if args.slack:
        enviar_slack(relatorio)

    if args.bq:
        salvar_metricas_bq(m)


if __name__ == "__main__":
    main()
