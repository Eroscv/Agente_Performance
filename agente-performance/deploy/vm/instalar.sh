#!/usr/bin/env bash
# deploy/vm/instalar.sh
#
# Instala a carga do BigQuery na VM (Oracle, Ubuntu) como timer do systemd.
# Rodar na VM, de dentro do clone do repositório, com o usuário que vai ser dono
# da carga (precisa de sudo para os arquivos do systemd). Pode rodar de novo:
# é idempotente.
#
#   bash agente-performance/deploy/vm/instalar.sh            # instala e ativa o timer
#   bash agente-performance/deploy/vm/instalar.sh --sem-ativar   # instala sem ativar
#
# Antes: .env copiado para agente-performance/.env e a chave da conta de serviço
# do BigQuery no caminho apontado por GOOGLE_APPLICATION_CREDENTIALS
# (ver deploy/vm/README.md).

set -euo pipefail

ATIVAR=1
[ "${1:-}" = "--sem-ativar" ] && ATIVAR=0

DIR_APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USUARIO="$(id -un)"
UNIDADE="agente-performance-sync"

falha() { echo "❌ $*" >&2; exit 1; }
ok() { echo "✅ $*"; }

[ "$USUARIO" = "root" ] && falha "rode com o usuário dono da carga, não como root"
command -v python3 >/dev/null || falha "python3 não encontrado (sudo apt install python3 python3-venv)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || falha "precisa de Python 3.10 ou mais novo"
command -v flock >/dev/null || falha "flock não encontrado (sudo apt install util-linux)"
command -v curl >/dev/null || falha "curl não encontrado (sudo apt install curl)"

# .env e credencial: só confere se existem, nunca imprime valores.
[ -f "$DIR_APP/.env" ] || falha "falta $DIR_APP/.env (copiar do notebook com scp)"
chmod 600 "$DIR_APP/.env"
CRED="$(grep -m1 '^GOOGLE_APPLICATION_CREDENTIALS=' "$DIR_APP/.env" | cut -d= -f2- | tr -d "\"'" || true)"
[ -n "$CRED" ] || falha "GOOGLE_APPLICATION_CREDENTIALS não está no .env"
[ -f "$CRED" ] || falha "credencial do BigQuery não encontrada no caminho do .env (ajuste GOOGLE_APPLICATION_CREDENTIALS para o caminho na VM)"
chmod 600 "$CRED"
ok ".env e credencial do BigQuery presentes"

# Ambiente Python isolado só com o que a carga usa.
[ -d "$DIR_APP/.venv" ] || python3 -m venv "$DIR_APP/.venv"
"$DIR_APP/.venv/bin/pip" install -q --upgrade pip
"$DIR_APP/.venv/bin/pip" install -q -r "$DIR_APP/deploy/vm/requirements-cron.txt"
ok "dependências instaladas em $DIR_APP/.venv"

mkdir -p "$DIR_APP/logs"

# Unidades do systemd com o usuário e o diretório desta VM.
for ext in service timer; do
    sed -e "s#__USUARIO__#$USUARIO#g" -e "s#__DIR_APP__#$DIR_APP#g" \
        "$DIR_APP/deploy/vm/$UNIDADE.$ext" | sudo tee "/etc/systemd/system/$UNIDADE.$ext" >/dev/null
done
sudo systemctl daemon-reload
ok "unidades $UNIDADE.service e .timer instaladas"

if [ "$ATIVAR" = 1 ]; then
    sudo systemctl enable --now "$UNIDADE.timer"
    ok "timer ativo. Próximas execuções:"
    systemctl list-timers "$UNIDADE.timer" --no-pager
else
    echo "Timer não ativado. Para testar uma carga agora: sudo systemctl start $UNIDADE.service"
fi
