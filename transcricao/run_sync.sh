#!/bin/bash
# Wrapper para o cron do OpenClaw
#
# Delega pro orquestrador.py (transcricao/agents/) em vez de encadear os
# 5 scripts na mão: ganha tratamento de erro entre etapas (uma falha não
# trava as seguintes, mas agora fica REGISTRADA — exit code != 0 se
# qualquer etapa falhou) e passa a rodar a Camada 4 (criar_tarefas_notion.py
# via ComunicacaoAgent), que não aparecia aqui antes.
VENV=/home/ubuntu/.openclaw/workspace/agente-performance/venv/bin/python3
DIR=/home/ubuntu/.openclaw/workspace/transcricao
LOG=$DIR/sync.log

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando pipeline de Transcrição..." >> "$LOG"
"$VENV" "$DIR/orquestrador.py" >> "$LOG" 2>&1
STATUS=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pipeline finalizado (exit code $STATUS)." >> "$LOG"
exit $STATUS
