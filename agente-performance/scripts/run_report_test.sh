#!/bin/bash
cd /home/ubuntu/.openclaw/workspace/agente-performance
source venv/bin/activate
export SLACK_CHANNEL=teste_performance
python scripts/cron_todos_relatorios.py --periodo diario
