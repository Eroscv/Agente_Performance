"""
scripts/sync_meta_semanal.py

Sync semanal automático — Meta Ads campaign_totals (semana fechada seg–dom).

Roda toda segunda-feira e carrega o período seg–dom da semana anterior
para todos os clientes Meta Ads ativos.

Janela de atribuição: 7d_click + 1d_view  (idêntica ao ads_diario)
Nível: campaign (sem arredondamento de ad-level)

Uso manual:
    python scripts/sync_meta_semanal.py
    python scripts/sync_meta_semanal.py --cliente source_of
    python scripts/sync_meta_semanal.py --semana-inicio 2026-10-12  # força semana específica
    python scripts/sync_meta_semanal.py --dry-run
"""

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from db.cron_log import registrar_run
from meta_ads.loader import buscar_totais_campaign, salvar_totais_campaign
from db.connections import PERFORMANCE_DBS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def semana_anterior() -> tuple[date, date]:
    """Retorna (segunda, domingo) da semana anterior."""
    hoje = date.today()
    # Vai para a segunda desta semana, depois volta 7 dias
    segunda_esta = hoje - timedelta(days=hoje.weekday())
    seg = segunda_esta - timedelta(days=7)
    dom = seg + timedelta(days=6)
    return seg, dom


def carregar_clientes() -> dict:
    with open(BASE_DIR / "clientes.json") as f:
        return json.load(f)["clientes"]


def sync_semanal_cliente(
    cliente_key: str,
    cfg: dict,
    date_start: date,
    date_end: date,
    dry_run: bool = False,
) -> bool | None:
    """Carrega campaign_totals da semana fechada para um cliente. Retorna True/False/None."""
    plataformas = cfg.get("plataformas", {})
    meta_cfg    = plataformas.get("meta_ads", {})

    if not meta_cfg.get("ativo", False):
        logger.info("[%s] meta_ads inativo — pulando", cliente_key)
        return None

    account_id = meta_cfg.get("meta_account_id")
    if not account_id:
        logger.warning("[%s] sem meta_account_id — pulando", cliente_key)
        return None

    tabela_diario = meta_cfg.get("tabela_db", f"{cliente_key}_meta_ads_diario")
    tabela_totais = tabela_diario.replace("_diario", "") + "_campaign_totals"
    token_env     = meta_cfg.get("meta_token_env", "META_ACCESS_TOKEN")
    db_path       = PERFORMANCE_DBS.get((cliente_key, "meta_ads"))

    logger.info(
        "[%s] Sync semanal campaign_totals | %s → %s | tabela=%s",
        cliente_key, date_start, date_end, tabela_totais,
    )

    if dry_run:
        logger.info("[%s] DRY RUN — sem escrita", cliente_key)
        return None

    run_id = registrar_run(
        cliente_key=cliente_key,
        periodo="semanal_meta_totals",
        data_inicio=str(date_start),
        data_fim=str(date_end),
        status="iniciado",
    )

    try:
        rows = buscar_totais_campaign(
            account_id=account_id,
            date_start=date_start,
            date_end=date_end,
            token_env=token_env,
        )

        if not rows:
            logger.warning("[%s] Nenhuma linha retornada pela API", cliente_key)
            registrar_run(
                cliente_key=cliente_key,
                periodo="semanal_meta_totals",
                data_inicio=str(date_start),
                data_fim=str(date_end),
                status="vazio",
                run_id=run_id,
            )
            return False

        salvas = salvar_totais_campaign(rows, tabela=tabela_totais, db_path=db_path)

        # Dual-write BQ (silencioso em falha)
        try:
            from db.bq_writer import write_campaign_totals, enabled
            if enabled():
                write_campaign_totals(rows, cliente=cliente_key)
        except Exception as bq_exc:
            logger.warning("[%s][BQ] falha silenciosa: %s", cliente_key, bq_exc)

        registrar_run(
            cliente_key=cliente_key,
            periodo="semanal_meta_totals",
            data_inicio=str(date_start),
            data_fim=str(date_end),
            status="sucesso",
            run_id=run_id,
        )
        logger.info(
            "[%s] ✅ %d linhas | %s → %s | R$ %.2f invest | R$ %.2f receita",
            cliente_key, salvas, date_start, date_end,
            sum(r["investimento"] for r in rows),
            sum(r["valor_compras"] for r in rows),
        )
        return True

    except Exception as exc:
        registrar_run(
            cliente_key=cliente_key,
            periodo="semanal_meta_totals",
            data_inicio=str(date_start),
            data_fim=str(date_end),
            status="erro",
            erro=str(exc),
            run_id=run_id,
        )
        logger.error("[%s] ❌ Erro no sync semanal: %s", cliente_key, exc)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Sync semanal Meta Ads campaign_totals (seg–dom fechado)"
    )
    parser.add_argument("--cliente",        type=str, default=None,
                        help="Chave do cliente (padrão: todos os ativos)")
    parser.add_argument("--semana-inicio",  type=str, default=None,
                        help="YYYY-MM-DD da segunda-feira da semana desejada "
                             "(padrão: semana anterior automática)")
    parser.add_argument("--dry-run",        action="store_true",
                        help="Busca dados mas não grava no banco")
    args = parser.parse_args()

    if args.semana_inicio:
        date_start = date.fromisoformat(args.semana_inicio)
        # Garante que é uma segunda-feira
        if date_start.weekday() != 0:
            logger.warning(
                "Data informada (%s) não é segunda-feira (weekday=%d). "
                "Ajustando para a segunda mais próxima anterior.",
                date_start, date_start.weekday()
            )
            date_start = date_start - timedelta(days=date_start.weekday())
        date_end = date_start + timedelta(days=6)
    else:
        date_start, date_end = semana_anterior()

    logger.info(
        "=== Sync Semanal Meta campaign_totals | %s (seg) → %s (dom) ===",
        date_start, date_end,
    )

    # Semana não pode incluir hoje ou amanhã (período ainda aberto)
    if date_end >= date.today():
        logger.error(
            "Semana solicitada (%s → %s) ainda não está fechada (hoje = %s). "
            "Use --semana-inicio para especificar uma semana passada.",
            date_start, date_end, date.today()
        )
        sys.exit(1)

    clientes = carregar_clientes()
    sucessos = erros = pulados = 0

    for key, cfg in clientes.items():
        if not cfg.get("ativo", False):
            continue
        if args.cliente and key != args.cliente:
            continue

        ok = sync_semanal_cliente(key, cfg, date_start, date_end, dry_run=args.dry_run)
        if ok is True:
            sucessos += 1
        elif ok is False:
            erros += 1
        else:
            pulados += 1

    logger.info(
        "Sync semanal Meta concluído: %d sucesso(s), %d erro(s), %d pulado(s)",
        sucessos, erros, pulados,
    )
    sys.exit(1 if erros > 0 and sucessos == 0 else 0)


if __name__ == "__main__":
    main()
