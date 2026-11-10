"""
scripts/sync_meta_diario.py

Sync diário automático — Meta Ads API → SQLite.

Executa para todos os clientes ativos com meta_ads ativo
e meta_account_id definido em clientes.json.

Uso manual:
    python scripts/sync_meta_diario.py
    python scripts/sync_meta_diario.py --dias 14
    python scripts/sync_meta_diario.py --cliente source_of
"""

import argparse
import json
import logging
import sys
from datetime import datetime, UTC
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from db.cron_log import registrar_run
from meta_ads.loader import carregar_meta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def carregar_clientes() -> dict:
    with open(BASE_DIR / "clientes.json") as f:
        return json.load(f)["clientes"]


def sync_cliente(cliente_key: str, cfg: dict, dias: int) -> bool | None:
    """Executa sync Meta Ads para um cliente. Suporta meta_account_id2 (segunda conta)."""
    plataformas = cfg.get("plataformas", {})
    meta_cfg    = plataformas.get("meta_ads", {})

    if not meta_cfg.get("ativo", False):
        logger.info("[%s] meta_ads inativo — pulando", cliente_key)
        return None

    account_id = meta_cfg.get("meta_account_id")
    if not account_id:
        logger.warning("[%s] sem meta_account_id — pulando", cliente_key)
        return None

    tabela    = meta_cfg.get("tabela_db", f"{cliente_key}_meta_ads_diario")
    token_env = meta_cfg.get("meta_token_env", "META_ACCESS_TOKEN")

    # DB de destino: performance/<cliente>/meta_ads.db (não o campanhas.db legado)
    from db.connections import PERFORMANCE_DBS
    from pathlib import Path
    db_path = PERFORMANCE_DBS.get((cliente_key, "meta_ads"))

    hoje = datetime.now(UTC).strftime("%Y-%m-%d")

    # Monta lista de contas: conta principal + conta2 (se configurada)
    contas = [(account_id, token_env)]
    account_id2 = meta_cfg.get("meta_account_id2")
    if account_id2:
        token_env2 = meta_cfg.get("meta_token_env2", token_env)  # reutiliza token se não tiver específico
        contas.append((account_id2, token_env2))

    resultado_final = True
    for acct_id, t_env in contas:
        label = meta_cfg.get("meta_account_id2_label", acct_id) if acct_id == account_id2 else acct_id
        logger.info("[%s] Iniciando sync Meta Ads (account=%s [%s], tabela=%s, dias=%d)",
                    cliente_key, acct_id, label, tabela, dias)

        run_id = registrar_run(
            cliente_key=cliente_key,
            periodo="diario_meta",
            data_inicio=hoje,
            data_fim=hoje,
            status="iniciado",
        )

        try:
            linhas, periodo = carregar_meta(
                account_id=acct_id,
                dias=dias,
                append=True,  # append=True para não sobrescrever a conta principal
                tabela=tabela,
                token_env=t_env,
                db_path=db_path,
                cliente_key=cliente_key,
            )
            registrar_run(
                cliente_key=cliente_key,
                periodo="diario_meta",
                data_inicio=hoje,
                data_fim=hoje,
                status="sucesso",
                run_id=run_id,
            )
            logger.info("[%s] ✅ %d linhas | account=%s | período: %s | tabela: %s",
                        cliente_key, linhas, acct_id, periodo, tabela)

        except Exception as exc:
            registrar_run(
                cliente_key=cliente_key,
                periodo="diario_meta",
                data_inicio=hoje,
                data_fim=hoje,
                status="erro",
                erro=str(exc),
                run_id=run_id,
            )
            logger.error("[%s] ❌ Erro no sync Meta account=%s: %s", cliente_key, acct_id, exc)
            resultado_final = False

    return resultado_final


def main():
    parser = argparse.ArgumentParser(description="Sync diário Meta Ads → SQLite")
    parser.add_argument("--dias",    type=int, default=8,
                        help="Janela de dias a puxar (padrão: 8)")
    parser.add_argument("--cliente", type=str, default=None)
    args = parser.parse_args()

    clientes = carregar_clientes()
    sucessos = erros = 0

    for key, cfg in clientes.items():
        if not cfg.get("ativo", False):
            continue
        if args.cliente and key != args.cliente:
            continue

        ok = sync_cliente(key, cfg, dias=args.dias)
        if ok is True:
            sucessos += 1
        elif ok is False:
            erros += 1

    logger.info("Sync Meta concluído: %d sucesso(s), %d erro(s)", sucessos, erros)
    # Qualquer cliente com erro faz o passo falhar no cron (antes só falhava se todos falhassem)
    sys.exit(1 if erros > 0 else 0)


if __name__ == "__main__":
    main()
