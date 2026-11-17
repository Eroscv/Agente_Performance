# Carga do BigQuery na VM Oracle

Tira a carga (`scripts/cron_master_sync.sh`) do notebook e põe numa VM sempre
ligada, como timer do systemd. Motivo: a carga parou em 07/11, 14/11 e 16/11
porque o notebook suspendeu, sem nenhum erro no BigQuery.

## O que muda em relação ao notebook

| | Notebook (hoje) | VM |
|---|---|---|
| Agendador | `crontab` (`0 */3 * * *`) | timer systemd, 0h, 3h, ..., 21h de Brasília |
| Horário perdido | perdido de vez | `Persistent=true`: roda assim que a VM volta |
| Execução sobreposta | possível | `flock`: a segunda sai sem rodar |
| Etapa que falha | segue para a próxima | tenta de novo (3 tentativas, 2 min entre elas) e segue |
| Fim com falha | sai 0, ninguém sabe | sai 1 e avisa no Slack (`SLACK_WEBHOOK_URL`) |
| Carga travada | roda para sempre | interrompida em 90 min |
| Carga parada | sem aviso | alerta de frescor no GitHub Actions (PR 3), fora da VM |

## Antes de começar (Vitor)

- [ ] VM Oracle definida, com Ubuntu 22.04+ e acesso SSH para o Eros.
- [ ] Usuário da carga criado na VM (ex.: `guara`), com sudo para instalar o systemd.
- [ ] Acesso de leitura da VM ao repositório no GitHub (deploy key). Pelo fluxo
      caixa cinza o repositório deve ficar no GitHub da Guará; hoje está em
      `Eroscv/Agente_Performance`.
- [ ] `semente.md` atualizado com onde ficam o `.env` e a chave da conta de serviço
      na VM (documento confidencial, nunca no Slack).

## Passo a passo (Eros)

1. **Clonar** na VM, com o usuário da carga:
   ```bash
   git clone git@github.com:Eroscv/Agente_Performance.git ~/Agente_Performance
   ```
2. **Copiar os segredos do notebook** (do notebook para a VM, por `scp`; nunca colar
   em chat):
   ```bash
   scp agente-performance/.env guara@IP_DA_VM:~/Agente_Performance/agente-performance/.env
   scp CAMINHO_LOCAL/bq-service-account.json guara@IP_DA_VM:~/.config/guara/bq-service-account.json
   ```
   Na VM, ajustar no `.env` o `GOOGLE_APPLICATION_CREDENTIALS` para
   `/home/guara/.config/guara/bq-service-account.json`. Aproveitar para incluir
   `SLACK_WEBHOOK_URL` (canal #alertas-tecnicos) e, quando existir,
   `META_BOTICA_ACCESS_TOKEN`.
3. **Instalar sem ativar:**
   ```bash
   bash ~/Agente_Performance/agente-performance/deploy/vm/instalar.sh --sem-ativar
   ```
4. **Testar uma carga completa** num intervalo entre horários do notebook (ex.: 13h
   ou 16h), para as duas máquinas não escreverem ao mesmo tempo:
   ```bash
   sudo systemctl start agente-performance-sync.service
   tail -f ~/Agente_Performance/agente-performance/logs/cron_master_sync.log
   ```
   Conferir no fim: `21 verificações, 0 com falha` e `cron_master_sync concluído`.
5. **Testar o alerta**, forçando uma falha: rodar uma vez com uma credencial inválida e
   1 tentativa só. Tem que chegar a mensagem no #alertas-tecnicos:
   ```bash
   cd ~/Agente_Performance/agente-performance
   GOOGLE_APPLICATION_CREDENTIALS=/nao/existe SYNC_TENTATIVAS=1 bash scripts/cron_master_sync.sh
   ```
6. **VM e notebook juntos por 24h.** Ativar o timer na VM
   (`bash .../deploy/vm/instalar.sh`) e, no notebook, mudar o `crontab` para 1h30 depois dos
   horários da VM, para as duas não escreverem ao mesmo tempo no BigQuery:
   `30 1-22/3 * * * /usr/bin/env bash .../scripts/cron_master_sync.sh`.
   Acompanhar na VM:
   ```bash
   systemctl list-timers agente-performance-sync.timer
   journalctl -u agente-performance-sync.service -n 50
   ```
7. **Desligar o notebook** só depois de 24h (8 execuções) sem falha na VM:
   - `crontab -e` e apagar a linha do `cron_master_sync.sh`;
   - `kill` do bloqueio de suspensão (`pgrep -f cron-agente`).

## Depois da virada

- O `.env` da VM passa a ser o oficial: os scripts `shopify_oauth_*.py` renovam
  tokens gravando nele. Token novo (ex.: Meta da Botica) vai no `.env` da VM.
- Atualizar código: `git pull` na VM. Mudança em `requirements-cron.txt` ou nas
  unidades: rodar o `instalar.sh` de novo.
- Pronto quando houver 7 dias seguidos sem lacuna maior que 4h entre cargas
  (consulta nos jobs de `load_gold` no BigQuery) e o alerta de frescor tiver
  disparado num teste real.

## Voltar atrás

Na VM: `sudo systemctl disable --now agente-performance-sync.timer`. No notebook:
recolocar no `crontab` a linha
`0 */3 * * * /usr/bin/env bash .../agente-performance/scripts/cron_master_sync.sh`.
Nada no BigQuery precisa ser desfeito: as cargas são MERGE e o gold é recalculado.
