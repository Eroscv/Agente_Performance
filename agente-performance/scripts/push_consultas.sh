#!/bin/bash
# scripts/push_consultas.sh
# Gera CONSULTAS.md, faz commit e push no GitHub.
# Executado automaticamente todo dia via cron do OpenClaw.

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="$REPO_DIR/venv/bin/python3"
SCRIPT="$REPO_DIR/scripts/gerar_readme_consultas.py"
README="$REPO_DIR/CONSULTAS.md"
GIT_TOKEN="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"
REMOTE_URL="https://${GIT_TOKEN}@github.com/Eroscv/Agente_Performance.git"

echo "[push_consultas] $(date -u '+%Y-%m-%d %H:%M UTC') — Iniciando..."

# 1. Sincroniza dados da planilha Google Sheets → SQLite
cd "$REPO_DIR"
echo "[push_consultas] Sincronizando planilha Google Sheets..."
"$VENV_PYTHON" -m db.loader_sheets

# 2. Gera o README
"$VENV_PYTHON" "$SCRIPT"

# 2. Configura identidade git (necessário em ambiente headless)
git config user.email "cesar@guaramarketing.com.br" 2>/dev/null || true
git config user.name  "Cesar — Agente de Performance" 2>/dev/null || true

# 3. Garante que o remote tem o token atualizado
git remote set-url origin "$REMOTE_URL"

# 4. Stage e commit (só se houver mudanças)
git add "$README"
if git diff --cached --quiet; then
    echo "[push_consultas] Sem mudanças no CONSULTAS.md — nada a fazer."
    exit 0
fi

HOJE=$(date -u '+%Y-%m-%d')
TOTAL=$(grep -oP 'Total de consultas.*\*\*\K[0-9]+' "$README" | head -1 || echo "?")
git commit -m "chore: atualizar CONSULTAS.md — ${HOJE} (${TOTAL} consultas total)"

# 5. Push
git push origin main
echo "[push_consultas] ✅ CONSULTAS.md atualizado e publicado no GitHub."
