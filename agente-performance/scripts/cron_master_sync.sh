#!/usr/bin/env bash
# scripts/cron_master_sync.sh
#
# Orquestra toda a cadeia de sync: renovação de tokens Shopify, receita Shopify,
# pedidos com UTM, Google Ads, Meta Ads, e reconstrução das camadas
# ingestao/correlacao no BigQuery.
#
# Pensado para rodar a cada 3h (timer systemd na VM, ver deploy/vm/). Cada etapa
# é tolerante a falha isolada (continua as próximas mesmo se uma etapa falhar) e
# tudo fica registrado no log.
#
# Fallback: etapa que falha é tentada de novo (SYNC_TENTATIVAS, padrão 3, com
# SYNC_ESPERA_S segundos entre tentativas, padrão 120). Se alguma etapa terminar
# falhando, o script sai com código 1 e, se SLACK_WEBHOOK_URL estiver no ambiente
# ou no .env, avisa no Slack quais etapas falharam. Carga sem falha também avisa
# (hora, duração e etapas), a menos que SLACK_AVISAR_SUCESSO=0.
#
# Uso:
#   bash scripts/cron_master_sync.sh

set -uo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR" || exit 1

LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/cron_master_sync.log"

SYNC_TENTATIVAS="${SYNC_TENTATIVAS:-3}"
SYNC_ESPERA_S="${SYNC_ESPERA_S:-120}"
SLACK_AVISAR_SUCESSO="${SLACK_AVISAR_SUCESSO:-1}"
FALHAS=()
ETAPAS_OK=0
RECUPERADAS=()   # etapas que só passaram numa nova tentativa

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Uma execução por vez: se a anterior ainda roda, esta sai sem fazer nada.
exec 9>"$LOG_DIR/.cron_master_sync.lock"
if ! flock -n 9; then
    log "⏭ execução anterior ainda em andamento — pulando esta"
    exit 0
fi

run_step() {
    local desc="$1"; shift
    local tentativa rc
    for ((tentativa = 1; tentativa <= SYNC_TENTATIVAS; tentativa++)); do
        log "▶ $desc (tentativa $tentativa/$SYNC_TENTATIVAS)"
        "$@" >> "$LOG_FILE" 2>&1
        rc=$?
        if [ "$rc" -eq 0 ]; then
            log "✅ $desc OK"
            ETAPAS_OK=$((ETAPAS_OK + 1))
            if [ "$tentativa" -gt 1 ]; then RECUPERADAS+=("$desc (tentativa $tentativa)"); fi
            return 0
        fi
        log "❌ $desc FALHOU (exit $rc)"
        if [ "$tentativa" -lt "$SYNC_TENTATIVAS" ]; then
            sleep "$SYNC_ESPERA_S"
        fi
    done
    log "❌ $desc falhou após $SYNC_TENTATIVAS tentativas — seguindo para próxima etapa"
    FALHAS+=("$desc")
}

# Posta no Slack (SLACK_WEBHOOK_URL do ambiente ou do .env). Quebra de linha no texto: \\n.
avisar_slack() {
    local url="${SLACK_WEBHOOK_URL:-}"
    if [ -z "$url" ] && [ -f "$BASE_DIR/.env" ]; then
        url="$(grep -m1 '^SLACK_WEBHOOK_URL=' "$BASE_DIR/.env" | cut -d= -f2- | tr -d "\"'")"
    fi
    [ -z "$url" ] && return 0
    curl -sS -m 15 -X POST -H 'Content-Type: application/json' \
        --data "{\"text\":\"$1\"}" "$url" >> "$LOG_FILE" 2>&1 || true
    echo >> "$LOG_FILE"
}

duracao() {
    printf '%dmin' $(( (SECONDS + 30) / 60 ))
}

avisar_falhas() {
    local lista
    lista="$(printf '• %s\\n' "${FALHAS[@]}")"
    avisar_slack ":x: *Carga do BigQuery ($(hostname)) terminou com falhas* em $(duracao)\\n${lista}Etapas OK: ${ETAPAS_OK}. Log: logs/cron_master_sync.log"
}

avisar_sucesso() {
    [ "$SLACK_AVISAR_SUCESSO" = "1" ] || return 0
    local extra=""
    if [ "${#RECUPERADAS[@]}" -gt 0 ]; then
        extra="\\nPassaram numa nova tentativa: $(printf '%s; ' "${RECUPERADAS[@]}")"
    fi
    avisar_slack ":white_check_mark: Carga do BigQuery ($(hostname)) concluída às $(date '+%H:%M') em $(duracao): ${ETAPAS_OK} etapas OK.${extra}"
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

# 6. Yampi/Dooki (POD Kombucha: poddi, pod-kombucha, pod8).
#    45 dias: reprocessa pedidos que mudaram de status (pago → cancelado/estornado).
run_step "Sync Dooki/Yampi" python3 -m dooki.sync --dias 45

# 7. Primeiro pedido por cliente da Shopify POD (regra de cliente novo do gold)
run_step "Sync histórico de clientes Shopify POD" python3 scripts/sync_shopify_clientes_historico.py --dias 45

# 8. Meta Ads semanal (campaign_totals + novos_clientes_semanal). Só na segunda-feira, as 4 semanas
#    fechadas mais recentes: o snapshot é insert-only/idempotente e uma semana perdida é recuperada.
if [ "$(date +%u)" = "1" ]; then
    run_step "Sync Meta semanal" python3 scripts/sync_meta_semanal.py --semanas 4
fi

# 9. Reconstrói camadas ingestao/ e correlacao/ no BigQuery
run_step "Reconstruir camadas BigQuery" python3 scripts/rebuild_bq_layers.py

if [ "${#FALHAS[@]}" -gt 0 ]; then
    log "===== cron_master_sync concluído com ${#FALHAS[@]} etapa(s) com falha ====="
    avisar_falhas
    exit 1
fi
log "===== cron_master_sync concluído ====="
avisar_sucesso
