#!/usr/bin/env bash
# Publica um repo da Guará no VPS da FEQ num commit fixo, como usuário guara (sem sudo).
#   deploy.sh <pasta-do-clone> <commit> [requirements.txt] [nome-do-app pathspec...]
#   Com pathspecs, publica só esses caminhos (ex.: guara-performance agente-performance transcricao ':!transcricao/credentials').
# Código vai por git archive (nunca o working tree, nunca token no servidor).
#
# Portão (AUD-10): antes de copiar, roda PORTAO no código do commit; falhou, nada é publicado.
# Saúde e volta automática (AUD-16): depois de trocar a pasta, reinicia SERVICO e roda SAUDE na VM;
# falhou, a versão anterior volta (com o estado do KEEP) e o serviço reinicia nela. Exit 1.
#   Padrões por app abaixo; sobrescreva por variável de ambiente. SEM_PORTAO=1 pula o portão (diga por quê no PR).
set -euo pipefail
SRC=$1; REV=$(git -C "$SRC" rev-parse --verify "$2^{commit}"); REQ=${3:-requirements.txt}
APP=${4:-$(basename "$SRC")}; PATHS=("${@:5}"); HOST=ubuntu@144.33.22.236
case $APP in
  guara-performance)
    : "${SERVICO:=guara-performance}" "${SAUDE:=curl -fsS -o /dev/null --max-time 10 127.0.0.1:8102/health}"
    : "${PORTAO:=cd agente-performance && ${PY:-python3} -m pytest -q -p no:cacheprovider tests/test_eval_bot.py tests/test_confiabilidade.py tests/test_relatorio_retido.py tests/test_tokens.py}" ;;
  reportana_api_shopify)
    : "${SERVICO:=guara-rptn}" "${SAUDE:=systemctl is-active --quiet guara-rptn}"
    : "${PORTAO:=for t in tests/test_*.py; do ${PY:-python3} \$t || exit 1; done}" ;;
esac
# Estado que sobrevive ao redeploy (fica na pasta do app). Acrescente o que cada sistema gravar.
KEEP=".env .venv data guara.db guara.db-wal guara.db-shm .anexos agente-performance/.env agente-performance/data transcricao/db landing_page/db/landing_page.db transcricao/.env transcricao/credentials leads_reportana.db leads_reportana.db-wal leads_reportana.db-shm reports"
SSH=(ssh -i ~/.ssh/oracle-b.key -o IdentitiesOnly=yes -o BatchMode=yes -o ServerAliveInterval=15 "$HOST")
echo "== $APP @ $REV"
if [ -n "${PORTAO:-}" ] && [ "${SEM_PORTAO:-}" != 1 ]; then
  P=$(mktemp -d); git -C "$SRC" archive "$REV" | tar -x -C "$P"
  (cd "$P" && eval "$PORTAO") || { echo "PORTÃO FALHOU: nada publicado ($PORTAO)"; rm -rf "$P"; exit 1; }
  rm -rf "$P"; echo "portão ok"
fi
TMP=/tmp/deploy-$APP-$REV.tar
git -C "$SRC" archive --format=tar "$REV" ${PATHS[@]+-- "${PATHS[@]}"} | "${SSH[@]}" "cat > $TMP && chmod 644 $TMP"
"${SSH[@]}" "sudo -u guara bash -s" <<REMOTE
set -euo pipefail
D=/home/guara/$APP; rm -rf \$D.new; mkdir -p \$D.new; tar -x -C \$D.new -f $TMP
if [ -d \$D ]; then for f in $KEEP; do [ -e \$D/\$f ] || continue; rm -rf \$D.new/\$f; mkdir -p \$(dirname \$D.new/\$f); mv \$D/\$f \$D.new/\$f; done; fi
rm -rf \$D.old; [ -d \$D ] && mv \$D \$D.old; mv \$D.new \$D
cd \$D; [ -d .venv ] || python3 -m venv .venv
.venv/bin/pip -q install --disable-pip-version-check -r $REQ
echo $REV > REVISION
echo "anterior: \$(cat \$D.old/REVISION 2>/dev/null || echo nenhum) | agora: \$(cat REVISION)"
REMOTE
"${SSH[@]}" "rm -f $TMP"
[ -n "${SERVICO:-}" ] || exit 0
"${SSH[@]}" "sudo systemctl restart $SERVICO; sleep ${ESPERA:-20}; $SAUDE" && { echo "saúde ok: $SERVICO"; exit 0; }
echo "SAÚDE FALHOU em $SERVICO: voltando à versão anterior"
"${SSH[@]}" "sudo -u guara bash -s" <<REMOTE
set -euo pipefail
D=/home/guara/$APP; [ -d \$D.old ] || { echo "sem \$D.old para voltar"; exit 1; }
for f in $KEEP; do [ -e \$D/\$f ] || continue; rm -rf \$D.old/\$f; mkdir -p \$(dirname \$D.old/\$f); mv \$D/\$f \$D.old/\$f; done
rm -rf \$D.falhou; mv \$D \$D.falhou; mv \$D.old \$D
echo "voltou para: \$(cat \$D/REVISION 2>/dev/null || echo desconhecida) | a que falhou ficou em \$D.falhou"
REMOTE
"${SSH[@]}" "sudo systemctl restart $SERVICO; sleep ${ESPERA:-20}; $SAUDE" && echo "versão anterior de pé" || echo "ATENÇÃO: a versão anterior também não ficou saudável"
exit 1
