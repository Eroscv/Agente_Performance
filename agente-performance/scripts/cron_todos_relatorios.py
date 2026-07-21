"""
scripts/cron_todos_relatorios.py

Dispara relatórios para TODOS os clientes ativos lidos do registry (clientes.json).
Adicionar um novo cliente = só editar clientes.json. Sem tocar neste script.

Cada cliente tem seu próprio bloco de erro isolado — falha de um não
impede o outro.

Uso:
    python scripts/cron_todos_relatorios.py --periodo semana
    python scripts/cron_todos_relatorios.py --periodo mes
    python scripts/cron_todos_relatorios.py --periodo diario
    python scripts/cron_todos_relatorios.py --cliente source_of --periodo semana
"""

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Formatadores ──────────────────────────────────────────────────────────────

def _fmt_brl(v) -> str:
    if v is None:
        return "—"
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "—"


def _fmt_x(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}x"
    except Exception:
        return "—"


def _fmt_n(v, decimais=0) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.{decimais}f}".replace(",", ".")
    except Exception:
        return "—"


# ── Período ───────────────────────────────────────────────────────────────────

def resolver_periodo(periodo: str):
    hoje = date.today()
    if periodo == "diario":
        ontem = hoje - timedelta(days=1)
        return ontem, ontem
    elif periodo == "semana":
        seg = hoje - timedelta(days=hoje.weekday() + 7)
        return seg, seg + timedelta(days=6)
    elif periodo == "mes":
        primeiro_este = hoje.replace(day=1)
        ultimo_mes = primeiro_este - timedelta(days=1)
        return ultimo_mes.replace(day=1), ultimo_mes
    raise ValueError(f"Período inválido: {periodo}")


# ── Slack fallback simples ────────────────────────────────────────────────────

def postar_slack_fallback(msg: str, max_tentativas: int = 3) -> bool:
    import urllib.request, json, time
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return False
    payload = json.dumps({"text": msg}).encode()
    for i in range(1, max_tentativas + 1):
        try:
            req = urllib.request.Request(webhook_url, data=payload,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return True
        except Exception as e:
            logger.warning("Slack webhook (tentativa %d): %s", i, e)
            if i < max_tentativas:
                time.sleep(2 ** i)
    return False


# ── Pipeline por cliente ──────────────────────────────────────────────────────

def executar_cliente(
    cliente_key: str,
    cfg: dict,
    periodo: str,
    data_inicio: date,
    data_fim: date,
    periodo_str: str,
    tipo_label: str,
    tipo_builder: str,
    publicar: bool,
) -> dict:
    """
    Executa loader + builder + Slack para um cliente.
    Retorna dict com resultado ou erro.
    """
    from db.cron_log import registrar_run

    nome = cfg.get("nome", cliente_key)
    emoji = cfg.get("slack_emoji", "📊")
    label = cfg.get("slack_label", nome)

    logger.info("--- [%s] %s ---", cliente_key, label)

    run_id = registrar_run(
        cliente_key=cliente_key,
        periodo=periodo,
        data_inicio=str(data_inicio),
        data_fim=str(data_fim),
        status="iniciado",
    )

    try:
        # ── Loader ────────────────────────────────────────────────────────
        from db.registry import get_loader, get_builder
        loader_fn = get_loader(cliente_key)
        try:
            loader_fn()
        except Exception as e:
            logger.warning("[%s] Loader falhou: %s — usando dados locais", cliente_key, e)

        # ── Builder ───────────────────────────────────────────────────────
        builder_fn = get_builder(cliente_key, tipo_builder)
        if tipo_builder == "mensal":
            resultado = builder_fn(data_inicio=data_inicio, data_fim=data_fim, publicar=publicar)
        else:
            resultado = builder_fn(data_inicio=data_inicio, data_fim=data_fim, publicar=publicar)

        if resultado.get("erro"):
            raise RuntimeError(resultado["erro"])

        # ── Slack âncora + thread ─────────────────────────────────────────
        from slack.poster import postar_relatorio
        ok_slack = postar_relatorio(resultado, periodo_str, tipo_label, cliente=cliente_key)
        if not ok_slack:
            postar_slack_fallback(
                f"{emoji} *{label} · Relatório {tipo_label}*\n_{periodo_str}_\n"
                f"Invest.: {_fmt_brl(resultado.get('resumo', {}).get('invest') or resultado.get('resumo', {}).get('total_invest'))}"
                f" · ROAS: {_fmt_x(resultado.get('resumo', {}).get('roas'))}"
            )

        url = resultado.get("url", "—")
        logger.info("[%s] Slack: %s | Notion: %s", cliente_key, "OK" if ok_slack else "FALHOU", url)

        registrar_run(
            cliente_key=cliente_key,
            periodo=periodo,
            data_inicio=str(data_inicio),
            data_fim=str(data_fim),
            status="sucesso",
            notion_url=url,
            slack_ok=ok_slack,
            run_id=run_id,
        )
        return {"ok": True, "resultado": resultado}

    except Exception as e:
        logger.error("[%s] ERRO: %s", cliente_key, e)
        postar_slack_fallback(
            f"❌ *{label} · Relatório {tipo_label} — ERRO*\n_{e}_",
            max_tentativas=3,
        )
        registrar_run(
            cliente_key=cliente_key,
            periodo=periodo,
            data_inicio=str(data_inicio),
            data_fim=str(data_fim),
            status="erro",
            erro=str(e),
            run_id=run_id,
        )
        return {"ok": False, "erro": str(e)}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Cron — Relatórios Todos os Clientes")
    parser.add_argument(
        "--periodo", "-p",
        choices=["diario", "semana", "mes"],
        default="semana",
    )
    parser.add_argument(
        "--no-notion",
        action="store_true",
        help="Gera relatórios mas não publica no Notion",
    )
    parser.add_argument(
        "--cliente",
        default="todos",
        help="Chave do cliente (ex: source_of, pod_kombucha) ou 'todos' (padrão)",
    )
    args = parser.parse_args()

    publicar     = not args.no_notion
    data_inicio, data_fim = resolver_periodo(args.periodo)
    periodo_str  = f"{data_inicio.strftime('%d/%m/%Y')} → {data_fim.strftime('%d/%m/%Y')}"
    tipo_label   = {"diario": "Diário", "semana": "Semanal", "mes": "Mensal"}.get(args.periodo, args.periodo.title())
    tipo_builder = "mensal" if args.periodo == "mes" else "semanal"

    logger.info("=== Cron Todos os Clientes — %s (%s) ===", tipo_label, periodo_str)

    # Carrega clientes do registry
    from db.registry import get_clientes_ativos, listar_clientes
    if args.cliente == "todos":
        clientes = get_clientes_ativos()
    else:
        todos = listar_clientes()
        if args.cliente not in todos:
            logger.error("Cliente '%s' não encontrado. Disponíveis: %s", args.cliente, list(todos))
            sys.exit(1)
        clientes = {args.cliente: todos[args.cliente]}

    resultados, erros = {}, {}

    for i, (cliente_key, cfg) in enumerate(clientes.items(), 1):
        logger.info("--- [%d/%d] %s ---", i, len(clientes), cfg.get("nome", cliente_key))
        res = executar_cliente(
            cliente_key=cliente_key,
            cfg=cfg,
            periodo=args.periodo,
            data_inicio=data_inicio,
            data_fim=data_fim,
            periodo_str=periodo_str,
            tipo_label=tipo_label,
            tipo_builder=tipo_builder,
            publicar=publicar,
        )
        if res["ok"]:
            resultados[cliente_key] = res
        else:
            erros[cliente_key] = res["erro"]

    # ── Sumário final ─────────────────────────────────────────────────────
    total    = len(resultados) + len(erros)
    ok_count = len(resultados)
    logger.info("=== Concluído: %d/%d com sucesso ===", ok_count, total)

    if erros:
        logger.error("Erros: %s", erros)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
