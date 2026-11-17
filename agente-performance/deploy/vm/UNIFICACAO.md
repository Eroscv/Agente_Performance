# Unificar as duas cargas do BigQuery

Levantamento de 17/11/2026. Hoje duas cargas gravam as mesmas tabelas do bronze
(`guara_performance`), com código diferente:

| | Notebook | VM Oracle |
|---|---|---|
| Branch | `main` | `migracao-vps-feq` |
| Conta no BigQuery | `agente-guara-bq` | `guara-vps-feq` |
| Agendador | `crontab` a cada 3h (`scripts/cron_master_sync.sh`) | timers `guara-*` do systemd (`scripts/ciclo_3h.py` e outros) |
| Clientes | Source Of, POD, Botica | Source Of, POD |
| Grava | Meta, Google, Shopify, checkouts, pedidos com UTM, Yampi, histórico de clientes, Meta semanal | Meta, Google, Shopify (2 dias), Meta semanal; outros timers podem gravar mais |
| Camadas silver e gold | `rebuild_bq_layers.py` ao fim de cada carga | não roda |
| Registro de carga (`ingestao.log_cargas_fontes`) | grava | não grava |
| Verificações extras | qualidade do BigQuery, comparação semanal com a API | qualidade contra a API no SQLite, metas das planilhas, saúde dos timers |

A carga da VM começou em 12/11. Como as duas usam a mesma chave no MERGE, vale o que rodou
por último.

## O que a da VM estragava (corrigido no PR 14, falta subir na VM)

- **Cliques do Meta:** gravava todos os cliques como cliques no link. Os dias que ela gravou
  por último ficaram sem cliques no link na camada tratada (05 a 09/11, recarregados em 17/11).
- **`v_performance_mensal`:** a cada carga semanal recriava a view a partir do bronze, com o
  fator 1,1215. A `main` tinha o mesmo defeito (com 1,1383); os dois saem: a view só muda pelo
  arquivo versionado e `apply_bq_sql.py`.
- **Segredo da Shopify** no código.

## Riscos que continuam enquanto as duas rodarem

- **`pedidos_diario`:** o `sync_pedidos_diario.py` da VM não manda tipo de cliente, e-mail,
  número do pedido nem UF. Se algum timer da VM rodar esse script, o MERGE apaga esses campos
  (foi o que aconteceu em 14/11 com o tipo de cliente da Source Of).
- **`dooki_pedidos_diario`:** virou view em 17/11. O `dooki/sync.py` da VM ainda grava nela e
  vai falhar.
- **Tokens da Shopify:** as duas máquinas renovam o token de cliente e gravam no próprio `.env`.
  Uma renovação pode invalidar o token da outra.
- **Gold:** só é recalculado pela carga do notebook. Se o notebook parar, a VM continua
  gravando o bronze e o gold fica parado (o alerta de frescor do GitHub avisa).

## Passo 0, já (para parar o vai e vem)

1. Merge do PR 14 na `migracao-vps-feq`.
2. Na VM: colocar `SHOPIFY_BOTICA_CLIENT_SECRET` e `SHOPIFY_POD_CLIENT_SECRET` no `.env`,
   `git pull` e reiniciar o ciclo de 3h.
3. Na VM: `systemctl list-timers 'guara-*'` e desligar qualquer timer que rode
   `sync_pedidos_diario.py`, `dooki.sync` ou `create_bq_views_dashboard*.py`.

## Unificação

Base: a `main`. Ela tem o modelo do BigQuery (procedures, views, migrações, registro de carga,
tabelas `raw_*`), o agendador com trava, novas tentativas e aviso no Slack, as unidades do
systemd para a VM e o alerta fora da máquina. A branch da VM tem código melhor de robustez e
de relatórios, mas a camada de dados dela está atrasada.

**Trazer da VM para a `main`:**
- loader da Meta: chave por conta, dia e anúncio, deduplicação no lote e "guarda os dias bons
  e falha" (`SyncIncompleto`);
- loader do Google: não filtrar campanhas removidas (a `main` deixa de atualizar o gasto delas);
- `bq_writer`: juntar linhas repetidas pela chave antes do MERGE (na `main`, uma chave repetida
  faz o MERGE inteiro falhar em silêncio);
- verificações do ciclo de 3h (qualidade contra a API, metas das planilhas) e o post de saúde;
- relatórios (um por loja, Slack sem repetição, conferência com a API), lendo o gold do
  BigQuery por `query_fed` (sem o gate `BQ_LEITURA` da VM; decisão de 17/11), `utils/llm`,
  base de conhecimento, guarda do Text-to-SQL, testes novos;
- transcrição: MongoDB opcional via `MONGODB_URI` (a `main` ainda tem a URI com senha no
  código; trocar a senha).

**Manter o da `main` e descartar o da VM:** `sync_pedidos_diario.py`, `dooki/sync.py`,
`sync_meta_semanal.py`, tudo em `bigquery/`, `create_bq_views_dashboard*.py`,
`rebuild_bq_layers.py`, fator 1,1383, cliques do Meta, registro de carga.

**Um agendador só:** o timer de `deploy/vm/` (`cron_master_sync.sh`) na VM, com os passos
extras do ciclo de 3h (verificações e renovação de token). Os outros timers da VM
(relatórios, sessões, saúde, transcrição) continuam, e as unidades deles passam a ser
versionadas em `deploy/vm/`. O `crontab` do notebook sai depois de 24h sem falha na VM.

## Decisões tomadas

- 17/11: o Agente de Performance e os relatórios leem o **gold do BigQuery** (PR 13), não o
  SQLite da VM. Nada novo lê `data/performance/*.db` direto.

## Decisões em aberto

- Botica: Meta ativo (`main`) ou pausado (`clientes.json` da VM).
- Janela: dia parcial de hoje a cada 3h (as duas já fazem) e quantos dias para trás (8 na `main`,
  2 no ciclo da VM).
- Modelo de LLM: Gemini primeiro (VM) ou Claude.
- Base de conhecimento: Notion (`main`) ou SQLite (VM).
- Renovação dos tokens da Shopify: qual script, considerando o `.env` do RPTN que a VM também
  atualiza.
