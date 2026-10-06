#!/bin/bash
# Wrapper para o cron do OpenClaw
VENV=/home/ubuntu/.openclaw/workspace/agente-performance/venv/bin/python3
DIR=/home/ubuntu/.openclaw/workspace/transcricao
LOG=$DIR/sync.log

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando sync..." >> "$LOG"
"$VENV" "$DIR/sync_reunioes.py" >> "$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Sync finalizado." >> "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando análise de sentimento..." >> "$LOG"
"$VENV" "$DIR/analisador_reunioes.py" >> "$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando gestão de tickets..." >> "$LOG"
"$VENV" "$DIR/gestor_alertas.py" --processar >> "$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Gestão de tickets finalizada." >> "$LOG"
