#!/bin/bash
# Verifica e renova token Shopify POD se necessário
# Rodado pelo cron diariamente

VENV=/home/ubuntu/.openclaw/workspace/agente-performance/venv/bin/python3
SCRIPT=/home/ubuntu/.openclaw/workspace/agente-performance/scripts/shopify_oauth_pod.py
LOG=/home/ubuntu/.openclaw/workspace/agente-performance/logs/shopify_pod_token.log

echo "[$(date '+%Y-%m-%d %H:%M:%S BRT')] Verificando token Shopify POD..." >> "$LOG"
"$VENV" "$SCRIPT" --status >> "$LOG" 2>&1
STATUS=$?

if [ $STATUS -ne 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S BRT')] ❌ Token inválido — renovação necessária (manual via OAuth)" >> "$LOG"
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S BRT')] Verificação concluída (exit=$STATUS)" >> "$LOG"
