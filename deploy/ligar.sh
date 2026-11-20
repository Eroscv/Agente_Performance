#!/usr/bin/env bash
# Liga no VPS da FEQ o que já tiver segredo no .env. Pode rodar de novo: só liga o que estiver pronto.
#   ~/guara-ops/deploy/ligar.sh            liga serviços + timers e faz a carga inicial dos dados
#   ~/guara-ops/deploy/ligar.sh sem-carga  pula a carga inicial
# ponytail: a carga inicial usa a janela padrão dos scripts mais --dias 90; histórico maior, rodar à mão.
set -euo pipefail
ssh -i ~/.ssh/oracle-b.key -o IdentitiesOnly=yes -o BatchMode=yes -o ServerAliveInterval=15 ubuntu@144.33.22.236 \
  "CARGA=${1:-com-carga} bash -s" <<'REMOTE'
set -uo pipefail
P=/home/guara/guara-performance/agente-performance/.env
R=/home/guara/reportana_api_shopify/.env
tem() { sudo grep -qE "^$2=.+" "$1"; }   # nunca imprime o valor
# "CHAVE=" vazio anula o padrão do código (os.getenv devolve ""); vira comentário até ser preenchido.
# E o contrário: "# CHAVE=valor" preenchido sem tirar o "# " volta a valer.
for f in $P $R; do sudo sed -i -E 's/^([A-Z0-9_]+)=$/# \1=/; s/^# ?([A-Z0-9_]+)=(.+)$/\1=\2/' $f; done

# RPTN: Shopify → Reportana
if tem $R SHOPIFY_ACCESS_TOKEN && tem $R REPORTANA_CLIENT_ID && tem $R REPORTANA_CLIENT_SECRET; then
  sudo systemctl enable --now guara-rptn && echo "guara-rptn: ligado (1º ciclo reenvia ~24 mil clientes; o POST é idempotente)"
else echo "guara-rptn: falta SHOPIFY_ACCESS_TOKEN, REPORTANA_CLIENT_ID ou REPORTANA_CLIENT_SECRET"; fi

# Dados: syncs não dependem do bot; carga inicial uma vez só por job (marca em /home/guara/.carga-<job>)
for par in sync_meta_diario:META_ACCESS_TOKEN sync_gads_diario:GOOGLE_ADS_REFRESH_TOKEN cron_shopify_sync:SHOPIFY_TOKEN \
           sync_shopify_sessions:SHOPIFY_TOKEN; do
  job=${par%%:*}; var=${par#*:}
  tem $P $var || { echo "dados $job: falta $var"; continue; }
  if [ "$CARGA" = com-carga ] && ! sudo test -e /home/guara/.carga-$job; then
    echo "dados $job: carga inicial (até 40 min)"
    args="--dias 90"; [ $job = sync_shopify_sessions ] && args=""
    sudo -u guara bash -c "cd ${P%/.env} && set -a && . ./.env && set +a && PYTHONPATH=/home/guara/guara-performance/transcricao:/home/guara/guara-performance mkdir -p data/locks && timeout 40m flock -n -E 0 data/locks/$job.lock ../.venv/bin/python scripts/$job.py $args" \
      >/tmp/carga-$job.log 2>&1 && { sudo -u guara touch /home/guara/.carga-$job; echo "  ok"; } || echo "  FALHOU: tail /tmp/carga-$job.log"
  fi
done
for t in sync-meta:META_ACCESS_TOKEN meta-semanal:META_ACCESS_TOKEN sync-gads:GOOGLE_ADS_REFRESH_TOKEN \
         sync-shopify:SHOPIFY_TOKEN sync-sessoes:SHOPIFY_TOKEN dashboard-ingest:DASHBOARD_INGEST_TOKEN \
         relatorio-diario:SLACK_BOT_TOKEN relatorio-pod:SLACK_BOT_TOKEN qualidade:META_ACCESS_TOKEN saude:SLACK_BOT_TOKEN tokens:SLACK_BOT_TOKEN carga-bq:GOOGLE_ADS_REFRESH_TOKEN; do
  tem $P ${t#*:} && sudo systemctl enable --now guara-${t%%:*}.timer >/dev/null 2>&1 && echo "timer ${t%%:*}: ligado"
done
if tem $P NOTION_API_KEY && tem $P GEMINI_API_KEY && sudo test -s /home/guara/guara-performance/transcricao/credentials/gdrive-service-account.json; then
  sudo systemctl enable --now guara-transcricao.timer >/dev/null && echo "timer transcricao: ligado"
else echo "transcricao: falta NOTION_API_KEY, GEMINI_API_KEY ou o JSON em transcricao/credentials/"; fi

# Bot de Performance + Landing Pages
if tem $P SLACK_BOT_TOKEN && tem $P SLACK_SIGNING_SECRET && tem $P GEMINI_API_KEY; then
  sudo systemctl enable --now guara-performance && echo "guara-performance: ligado"
  sleep 5; curl -s -o /dev/null -w "  /health interno: %{http_code}\n" 127.0.0.1:8102/health
else echo "guara-performance: falta SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET ou GEMINI_API_KEY"; fi

echo "--- Atena: $(systemctl is-active atena) | $(free -m | awk '/Mem/{print $7}') MB disponíveis"
REMOTE
curl -s -o /dev/null -w "feq.vitoraugolis.com.br: %{http_code}\n" --max-time 20 https://feq.vitoraugolis.com.br/
curl -s -o /dev/null -w "guara /health público: %{http_code}\n" --max-time 20 https://guara.144.33.22.236.sslip.io/health
