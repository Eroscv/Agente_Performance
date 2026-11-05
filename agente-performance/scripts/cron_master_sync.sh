#!/usr/bin/env bash
# scripts/cron_master_sync.sh
#
# Orquestra toda a cadeia de sync: renovação de tokens Shopify, receita Shopify,
# pedidos com UTM, Google Ads, Meta Ads, e reconstrução das camadas
# ingestao/correlacao no BigQuery.
#
# Pensado para rodar via cron a cada poucas horas. Cada etapa é tolerante a
# falha isolada (continua as próximas mesmo se uma etapa falhar) e tudo fica
# registrado no log.
#
# Uso:
#   bash scripts/cron_master_sync.sh

set -uo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR" || exit 1

LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/cron_master_sync.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

run_step() {
    local desc="$1"; shift
    log "▶ $desc"
    if "$@" >> "$LOG_FILE" 2>&1; then
        log "✅ $desc OK"
    else
        log "❌ $desc FALHOU (exit $?) — seguindo para próxima etapa"
    fi
}

log "===== cron_master_sync iniciado ====="

# 1. Renovação de tokens Shopify (auto-renova se expirado; silencioso se OK)
run_step "Renovar token Shopify POD"       python3 scripts/shopify_oauth_pod.py --status
run_step "Renovar token Shopify Botica"    python3 scripts/shopify_oauth_botica.py --status
run_step "Renovar token Shopify Source Of" python3 scripts/shopify_oauth_source_of.py --status

# 2. Receita Shopify (todos os clientes)
run_step "Sync receita Shopify" python3 scripts/cron_shopify_sync.py --dias 8

# 3. Pedidos com UTM (ROAS pago/CAC por anúncio)
run_step "Sync pedidos_diario" python3 scripts/sync_pedidos_diario.py --dias 8

# 4. Google Ads
run_step "Sync Google Ads" python3 scripts/sync_gads_diario.py --dias 8

# 5. Meta Ads (requer META_ACCESS_TOKEN configurado — falha graciosamente se vazio)
run_step "Sync Meta Ads" python3 scripts/sync_meta_diario.py --dias 8

# 6. Checkout Yampi/Dooki (POD Kombucha)
run_step "Sync Dooki/Yampi" python3 -m dooki.sync --dias 8

# 7. Reconstrói camadas ingestao/ e correlacao/ no BigQuery
run_step "Reconstruir camadas BigQuery" python3 scripts/rebuild_bq_layers.py

log "===== cron_master_sync concluído ====="
