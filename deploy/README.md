# Kit de deploy do VPS 1 (VPS da FEQ, 144.33.22.236)

Versionado aqui pela AUD-02: antes vivia só no Mac (`~/guara-ops/deploy`). Em 17/11/2026 as units e o
`backup_guara.py` daqui eram idênticos (sha256) aos instalados em `/etc/systemd/system/` e `/home/guara/shim/`.

| Arquivo | Vai para | O que é |
|---|---|---|
| `deploy.sh` | roda no Mac | publica um commit (git archive), com portão de testes antes e saúde + volta automática depois |
| `ligar.sh` | roda no Mac | liga serviços e timers que já têm segredo no `.env` |
| `guara-performance.service` | `/etc/systemd/system/` | bot + landing pages (127.0.0.1:8102) |
| `timers/*` | `/etc/systemd/system/` | jobs agendados (`guara-job@<script>.service`) |
| `backup_guara.py`, `shim/tz.py` | `/home/guara/shim/` | backup diário dos SQLite (inclui o RPTN) e fuso |
| `performance.env.modelo` | referência | chaves do `.env`, todas vazias; os valores ficam só na VM |

A unit do RPTN fica no repositório dele (`Eroscv/reportana_api_shopify`, `deploy/guara-rptn.service`).

Timers presentes na VM mas **desligados** de propósito: `guara-dashboard-ingest` (sem `DASHBOARD_INGEST_TOKEN`) e
`guara-relatorio-pod` (envia relatório ao cliente sem a conferência do `relatorio_diario`; ver AUD do plano de QA, 1.6).

## Publicar

```bash
PY=~/caminho/venv/bin/python deploy/deploy.sh ~/guara-repos/Agente_Performance <commit> agente-performance/requirements.txt guara-performance
PY=~/caminho/venv/bin/python deploy/deploy.sh ~/guara-repos/reportana_api_shopify <commit>
```

- **Caminhos:** o `guara-performance` publica por padrão `agente-performance`, `transcricao` e `landing_page` (sem `transcricao/credentials`).
- **Portão:** roda os testes do commit no Mac (`PY` com `requirements-dev.txt`). Falhou, nada sai.
- **Saúde:** reinicia o serviço e checa `/health`. Falhou, a versão anterior volta sozinha e a que falhou fica em `<app>.falhou`.
- Unit ou timer novo/alterado: copiar para `/etc/systemd/system/`, `systemctl daemon-reload` e `ligar.sh sem-carga`.

## Carga do BigQuery (guara-carga-bq)

Desde 20/11/2026 a carga do BigQuery (`scripts/cron_master_sync.sh`: Shopify, Meta, Google, Yampi/Dooki,
histórico de clientes e camadas silver/gold) roda na VM, não mais no notebook. Ela usa o código da
**main**, numa pasta separada do guara-performance (que roda a `migracao-vps-feq`):

```bash
deploy/deploy.sh ~/guara-ops/Agente_Performance origin/main agente-performance/deploy/vm/requirements-cron.txt guara-bq agente-performance
# só na primeira vez: mesmo .env do guara-performance (segredos e tokens Shopify renovados pelo guara-shopify-token.timer)
ssh ... 'sudo -u guara ln -sfn /home/guara/guara-performance/agente-performance/.env /home/guara/guara-bq/agente-performance/.env'
```

- Timer `guara-carga-bq.timer`: 02:10, 05:10, … UTC (fora dos syncs da VM e do ciclo de 3h); `MemoryMax=300M`.
- Falha aparece no post de saúde (rotina `carga-bq`) e no alerta de frescor do GitHub Actions (`alerta-frescor-bq.yml`).
- Log: `/home/guara/guara-bq/agente-performance/logs/cron_master_sync.log`.
- O crontab do notebook foi desligado: duas máquinas não devem carregar ao mesmo tempo.
