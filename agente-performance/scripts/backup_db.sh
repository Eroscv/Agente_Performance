#!/usr/bin/env bash
# scripts/backup_db.sh
# Backup automático dos bancos SQLite do Agente de Performance Guará
# Roda a cada 3 dias via cron do OpenClaw
# Mantém os últimos 10 backups (30 dias aprox), remove os mais antigos

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$BASE_DIR/data"
BACKUP_DIR="$BASE_DIR/data/backups"
LOG="$BASE_DIR/logs/backup.log"
MAX_BACKUPS=10

mkdir -p "$BACKUP_DIR"
mkdir -p "$(dirname "$LOG")"

DATE=$(date -u +"%Y-%m-%d_%H%M")
echo "[$DATE UTC] Iniciando backup..." | tee -a "$LOG"

# Bancos para backup
DBS=("campanhas.db" "cache.db" "context.db" "logs.db")
TOTAL=0

for DB in "${DBS[@]}"; do
    SRC="$DATA_DIR/$DB"
    if [ ! -f "$SRC" ]; then
        echo "  ⚠️  $DB não encontrado, pulando" | tee -a "$LOG"
        continue
    fi

    DEST="$BACKUP_DIR/${DATE}_${DB}"

    # sqlite3 .backup garante consistência mesmo com WAL ativo
    sqlite3 "$SRC" ".backup '$DEST'"

    SIZE=$(du -sh "$DEST" | cut -f1)
    echo "  ✅ $DB → $DEST ($SIZE)" | tee -a "$LOG"
    TOTAL=$((TOTAL + 1))
done

# Limpar backups antigos — manter apenas os MAX_BACKUPS mais recentes por arquivo
for DB in "${DBS[@]}"; do
    # Listar backups deste DB ordenado por data (mais antigo primeiro)
    mapfile -t OLD < <(ls -1t "$BACKUP_DIR"/*_"$DB" 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)))
    for F in "${OLD[@]}"; do
        echo "  🗑️  Removendo backup antigo: $(basename "$F")" | tee -a "$LOG"
        rm -f "$F"
    done
done

echo "[$DATE UTC] Backup concluído — $TOTAL arquivos salvos em $BACKUP_DIR" | tee -a "$LOG"
echo "---" >> "$LOG"
