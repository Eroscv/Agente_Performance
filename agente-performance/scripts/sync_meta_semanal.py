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
    """Carrega campaign_totals da semana fechada para um cliente. Suporta meta_account_id2."""
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

    # Monta lista de contas: principal + conta2 (se configurada)
    contas = [(account_id, token_env)]
    account_id2 = meta_cfg.get("meta_account_id2")
    if account_id2:
        token_env2 = meta_cfg.get("meta_token_env2", token_env)
        contas.append((account_id2, token_env2))

    resultado_final = True
    all_rows = []

    for acct_id, t_env in contas:
        label = meta_cfg.get("meta_account_id2_label", acct_id) if acct_id == account_id2 else acct_id
        run_id = registrar_run(
            cliente_key=cliente_key,
            periodo="semanal_meta_totals",
            data_inicio=str(date_start),
            data_fim=str(date_end),
            status="iniciado",
        )

        try:
            rows = buscar_totais_campaign(
                account_id=acct_id,
                date_start=date_start,
                date_end=date_end,
                token_env=t_env,
            )

            if not rows:
                logger.warning("[%s] Nenhuma linha retornada pela API (account=%s)", cliente_key, acct_id)
                registrar_run(
                    cliente_key=cliente_key,
                    periodo="semanal_meta_totals",
                    data_inicio=str(date_start),
                    data_fim=str(date_end),
                    status="vazio",
                    run_id=run_id,
                )
                continue

            salvas = salvar_totais_campaign(rows, tabela=tabela_totais, db_path=db_path)
            all_rows.extend(rows)

            # Dual-write BQ (silencioso em falha)
            try:
                from db.bq_writer import write_campaign_totals, enabled
                if enabled():
                    write_campaign_totals(rows, cliente=cliente_key)
            except Exception as bq_exc:
                logger.warning("[%s][BQ] falha silenciosa (account=%s): %s", cliente_key, acct_id, bq_exc)

            # Snapshot novos clientes semanal — INSERT-only, semana fechada nunca sobrescrita
            try:
                from db.bq_writer import write_novos_clientes_semanal, enabled as bq_enabled
                if bq_enabled():
                    # Busca dados de novos_clientes da shopify_diario para a semana fechada
                    import os as _os
                    from google.cloud import bigquery as _bq
                    _bq_client = _bq.Client()
                    _proj = _os.environ.get("BQ_PROJECT", _bq_client.project)
                    _ds   = _os.environ.get("BQ_DATASET", "guara_performance")
                    _sql  = f"""
                        SELECT
                            '{cliente_key}'                        AS cliente,
                            DATE '{date_start}'                    AS semana_inicio,
                            DATE '{date_end}'                      AS semana_fim,
                            SUM(novos_clientes)                    AS novos_clientes,
                            SUM(recompradores)                     AS recompradores,
                            SUM(pedidos)                           AS pedidos,
                            SUM(novos_meta)                        AS novos_meta,
                            SUM(novos_google)                      AS novos_google,
                            SUM(novos_outros)                      AS novos_outros
                        FROM `{_proj}.{_ds}.shopify_diario`
                        WHERE cliente = '{cliente_key}'
                          AND data BETWEEN DATE '{date_start}' AND DATE '{date_end}'
                    """
                    _rows = list(_bq_client.query(_sql).result())
                    if _rows and _rows[0].pedidos:
                        _snap = [{
                            "semana_inicio": str(date_start),
                            "semana_fim":    str(date_end),
                            "novos_clientes": _rows[0].novos_clientes,
                            "recompradores":  _rows[0].recompradores,
                            "pedidos":        _rows[0].pedidos,
                            "novos_meta":     _rows[0].novos_meta,
                            "novos_google":   _rows[0].novos_google,
                            "novos_outros":   _rows[0].novos_outros,
                        }]
                        write_novos_clientes_semanal(_snap, cliente=cliente_key)
            except Exception as _snap_exc:
                logger.warning("[%s][BQ] novos_clientes_semanal falha silenciosa: %s", cliente_key, _snap_exc)

            registrar_run(
                cliente_key=cliente_key,
                periodo="semanal_meta_totals",
                data_inicio=str(date_start),
                data_fim=str(date_end),
                status="sucesso",
                run_id=run_id,
            )
            logger.info(
                "[%s] ✅ %d linhas | account=%s [%s] | %s → %s | R$ %.2f invest | R$ %.2f receita",
                cliente_key, salvas, acct_id, label, date_start, date_end,
                sum(r["investimento"] for r in rows),
                sum(r["valor_compras"] for r in rows),
            )

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
            logger.error("[%s] ❌ Erro no sync semanal (account=%s): %s", cliente_key, acct_id, exc)
            resultado_final = False

    return resultado_final if all_rows else False


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
    sys.exit(1 if erros else 0)  # falha parcial também falha a unit (antes: só se tudo falhasse)


if __name__ == "__main__":
    main()
