# ⚙️ Arquitetura & Decisões em Aberto — v2 (out/2026)

> Documento para revisão com Vitor. Reflete o estado real do sistema em produção.
> Mantido em: `agente-performance/docs/arquitetura-decisoes-v2.md`

---

## Estado atual do sistema (o que já roda)

Um único processo Python (`main.py`) na VPS `44.204.251.251`, porta 8001, gerenciado pelo systemd (`agente-guara.service`). Não são dois agentes separados — o doc técnico anterior estava desatualizado. O projeto `transcricao-510015` foi absorvido; o banco de transcrições agora é um SQLite local federado ao banco principal.

**Clientes ativos:**
- Source OF — Meta Ads + Google Ads + Shopify
- POD Kombucha — Meta Ads + Google Ads (ativado em 06/10) + Shopify + Dooki

**Plataformas de dados:**
- SQLite local (múltiplos bancos federados via DuckDB)
- BigQuery (`guara-db.guara_performance`) — espelho analítico
- Slack — entrada de perguntas e saída de respostas/alertas
- Notion — publicação de relatórios

---

## 1. Processo único na VPS

**O que existe:**

O systemd garante o restart automático com `Restart=always` e `RestartSec=5`. Se o processo cair por qualquer motivo, ele sobe em 5 segundos. Isso já está em produção e funcionou no período de testes.

**Isolamento de erros entre subagentes:**

No modelo atual, não há subagentes como processos separados — há handlers assíncronos dentro do mesmo processo FastAPI. Cada requisição Slack tem seu próprio contexto `async`, então um erro numa consulta de campanha não interfere numa consulta paralela de outro cliente. O ponto fraco: um erro que trava o event loop principal (ex: deadlock de banco) derruba tudo. Mitigação já existente: timeouts nos handlers Slack e `try/except` cobrindo o engine.

**Webhook x LLM:**

O Slack Bolt faz `ack()` imediato ao receber o evento — antes de qualquer chamada ao Claude. O Slack exige resposta em 3 segundos; o `ack()` satisfaz isso. A resposta com conteúdo vem depois, via `say()` assíncrono. Isso já funciona assim em produção.

**Persistência após restart:**

Toda a memória operacional fica em SQLite (disco). Isso inclui: contexto de thread (`context.db`), logs de sync, alertas de sync, cache de respostas (`cache.db`). O restart do processo não perde nada. O único dado em memória volátil é o cache em RAM (`agent/cache.py`) — que é reconstruído na próxima consulta.

**Em aberto / decisão pendente:**

- O modelo atual não tem subagentes como processos isolados. Se a necessidade for isolamento forte (ex: subagente de transcrição que consome muito CPU sem impactar o agente de consultas), a arquitetura correta é dois serviços systemd distintos com comunicação por SQLite ou fila. Isso ainda não existe — é uma decisão que precisa ser tomada.

---

## 2. Motor de alertas

**O que existe hoje:**

O único motor de alertas implementado é o `db/sync_alerts.py` — que detecta quando um sync de dados falha 2 vezes seguidas e envia alerta Slack. Isso cobre alertas de infraestrutura, não alertas de atendimento.

**O que está em aberto (e precisa ser construído):**

Os 4 alertas do documento anterior (sem resposta, urgente, acesso, sentimento) ainda não existem como código. São requisitos, não realidade.

**Problema do "sem resposta":**

Este alerta nunca pode ser disparado por uma mensagem recebida — porque nesse caso, nenhuma mensagem chega. A única solução é um loop agendado que varre a tabela de conversas abertas e compara o `last_message_at` com o prazo configurado por cliente. Esse cron ainda não existe.

**Proposta de implementação:**

```
Cron a cada 15 min:
  1. Lê tabela conversations WHERE status='aberto'
  2. Para cada conversa: verifica se (now - last_message_at) > prazo_sla[cliente]
  3. Se sim e ainda não alertado: dispara alerta Slack no canal do cliente
  4. Marca como alertado com timestamp
```

O prazo SLA precisa ser configurado por cliente em `clientes.json`. Sugestão de padrão: 4h úteis.

**Escopo dos 4 alertas — situação real:**

| Alerta | Status | Observação |
|--------|--------|------------|
| Sem resposta | ❌ não existe | Precisa do cron acima |
| Urgente | ❌ não existe | Precisa do classificador + critério |
| Acesso (cliente sem conseguir usar) | ❌ não existe | Menos prioritário |
| Sentimento negativo | ❌ não existe | Depende do classificador estar rodando |

Recomendação: implementar "sem resposta" e "urgente" primeiro. São os que têm impacto direto no cliente.

---

## 3. Aviso de indisponibilidade

**O problema real:**

Se a VPS desligar, o processo que avisaria também desligou. Qualquer solução que depende da VPS é inútil aqui.

**Solução sem custo adicional:**

UptimeRobot (plano gratuito) faz ping no endpoint `/health` do agente a cada 5 minutos. Se receber erro ou timeout, dispara webhook para um canal Slack configurado na conta da Guará. Isso é externo à VPS e funciona independente do estado do servidor.

**Implementação:**

1. Criar monitor no UptimeRobot apontando para `https://44.204.251.251:8001/health` (ou domínio)
2. Configurar alertas para webhook Slack no canal `#infra` (ou equivalente)
3. Nenhum código adicional na VPS é necessário

**Alternativa mais robusta:** Cloudflare Health Checks (já disponível se o domínio estiver na Cloudflare). Mesma lógica, configuração no painel Cloudflare.

---

## 4. Duplicidade no BigQuery

**Por que as duplicatas existem:**

O `db/bq_writer.py` usa `insert_rows_json` — que é append puro, sem deduplicação. Não há MERGE. Cada vez que o sync roda e encontra linhas que já existem no SQLite, ele reenvia para o BQ como linhas novas. Isso é um bug de design.

**Quando uma linha muda no SQLite, ela sobe de novo?**

Sim. O sync retroativo dos últimos 8 dias roda diariamente. Se qualquer linha do SQLite mudar (ex: Meta Ads retroativamente ajusta uma conversão), ela é reenviada ao BQ como linha nova. Resultado: 8 linhas para o mesmo dia ao longo de uma semana de syncs.

**Correção atual (paliativa):**

As views `v_performance_mensal` e similares já usam uma CTE `shopify_dedup` com `ROW_NUMBER() OVER (PARTITION BY cliente, data ORDER BY criado_em DESC)` para ignorar duplicatas. Isso corrige o impacto nas consultas, mas não o dado bruto.

**Correção definitiva necessária:**

Migrar o writer para usar `MERGE` (DML do BigQuery). Estrutura:
```sql
MERGE target USING source ON (target.cliente = source.cliente AND target.data = source.data)
WHEN MATCHED THEN UPDATE SET ...
WHEN NOT MATCHED THEN INSERT ...
```

Bloqueio atual: `MERGE` não funciona sobre o streaming buffer (linhas inseridas há menos de ~90 minutos). A solução é usar `load_table_from_json` com `WRITE_TRUNCATE` por partição, ou migrar para batch writes ao invés de streaming.

**Mensagens e alertas sem `fonte_id`:**

Não têm deduplicação garantida no BQ. As tabelas `metricas_sla` e `tickets_alerta` foram criadas sem campo de dedup. Cada insert é uma linha nova. Para essas tabelas, a solução correta é um campo `fonte_id` derivado de um hash dos campos únicos (ex: `SHA256(cliente || data || tipo_alerta)`).

**Por que reunião aparece em dois datasets:**

`transcricao.guara_performance` era o projeto GCP original das transcrições. `guara_performance.analise_reunioes` é onde os dados foram migrados quando o pipeline foi consolidado. O projeto `transcricao-510015` ainda existe como origem mas os dados devem fluir apenas para `guara_performance`. **Decisão pendente:** deprecar ou remover o dataset de origem para evitar confusão.

---

## 5. Tickets como na Flora

**O problema de fechar por texto:**

Se a resolução é identificada por código/regex, qualquer mensagem positiva poderia fechar o ticket incorretamente. "Bom dia, tudo bem?" fecha o caso se o código procurar por "bom". Isso não funciona.

**Abordagem correta:**

O fechamento precisa combinar três sinais:
1. **LLM classifica a mensagem** como "resolução confirmada" (não só positivo — especificamente uma resolução)
2. **A pessoa que abriu o ticket** (ou alguém da equipe) envia a mensagem (não qualquer pessoa do grupo)
3. **Timeout automático**: se não houve mensagem nova em 48h, o ticket vai para "presumivelmente resolvido" e aguarda revisão

O LLM precisa de um prompt específico: *"Esta mensagem indica que o problema anterior foi resolvido? Responda apenas SIM ou NÃO."* — com o contexto da thread como input.

**Múltiplas demandas no mesmo grupo:**

Cada ticket precisa estar vinculado a uma thread Slack específica (`thread_ts`). Se o cliente abre uma demanda no grupo geral (não em thread), o agente cria uma thread e pede para continuar lá. Múltiplas threads = múltiplos tickets em paralelo, sem interferência.

Estrutura de dados mínima:
```
tickets(
  id, cliente_key, thread_ts, canal_slack,
  status,           -- aberto | aguardando_cliente | presumido_resolvido | fechado
  aberto_por,       -- user_id Slack
  aberto_em,        -- timestamp
  ultimo_evento_em, -- timestamp
  resumo            -- gerado por LLM na abertura
)
```

**Em aberto:** definir quem pode fechar um ticket (só a equipe Guará, ou o próprio cliente também?).

---

## 6. Critério de urgência

**Para reuniões (transcrições e Notion):**

Uma reunião é urgente se contém:
- Menção a prazo com data específica (ex: "até sexta", "antes do lançamento")
- Decisão que bloqueia outra pessoa ou equipe
- Problema em produção / bug reportado pelo cliente

Proposta de threshold: 2 dos 3 critérios presentes → urgente.

**Para WhatsApp / Slack:**

Uma mensagem é urgente se:
- Contém palavras de escalada: "travado", "parado", "urgente", "preciso hoje", "cliente reclamando"
- Vem de um contato de alto valor (configurável por cliente em `clientes.json`)
- Está sem resposta há mais de 2h em horário comercial (08h-18h BRT)

Proposta de threshold: qualquer 1 dos 3 → urgente. O 3° critério (sem resposta) depende do cron do item 2.

**Como medir a meta de 90% dos urgentes:**

O problema com amostra aleatória de 200 mensagens é que urgentes são raros (~5-10%). Em 200 mensagens, aparecem 10-20 urgentes — margem de erro muito alta.

Abordagem correta:
1. **Sample estratificado**: pegar as últimas 50 mensagens classificadas como urgente pelo sistema + revisão manual das 50
2. **Precision/recall separados**: precision = dos que o sistema marcou urgente, quantos são realmente urgentes. Recall = dos que são urgentes, quantos o sistema capturou.
3. **Meta real**: precision ≥ 85% (sem excesso de falsos positivos que cansem a equipe) + recall ≥ 90% (não perder os que importam)
4. **Ciclo de ajuste**: revisar os falsos negativos e adicionar os padrões perdidos ao classificador a cada 2 semanas no início

---

## 7. Lista completa de subagentes / módulos

**Já existem (em produção):**

| Módulo | Função | Arquivo |
|--------|--------|---------|
| NLP / Parse | Interpreta perguntas em português, extrai intent+datas | `agent/nlp.py` |
| Query Engine | 20+ consultas SQL por intent | `agent/queries/` |
| Comparative | WoW, MoM, períodos, ranking | `agent/comparatives.py` |
| Response Formatter | Formata resposta final com Claude Haiku | `agent/claude_client.py` |
| Sync Meta Ads | Busca e grava dados Meta | `meta_ads/loader.py` |
| Sync Google Ads | Busca e grava dados Google | `google_ads/loader.py` |
| Sync Shopify | Busca e grava receita/pedidos | `shopify/sync.py` |
| Sync Dooki | Busca pedidos POD Kombucha | `dooki/sync.py` |
| BQ Writer | Espelha dados no BigQuery | `db/bq_writer.py` |
| Relatório Diário | Gera e publica relatório Notion | `relatorio/builder_diario.py` |
| Relatório Semanal | Gera Excel semanal | `scripts/gerar_excel_semanal.py` |
| Sync Alerts | Alerta de falhas de sync | `db/sync_alerts.py` |
| Context Store | Memória de thread (SQLite TTL 24h) | `agent/context_store.py` |
| Slack Handlers | Recebe eventos Slack | `slack/handlers.py` |

**Faltam (decisão pendente de escopo):**

| Módulo | Função | Prioridade |
|--------|--------|------------|
| Cron "sem resposta" | Varre prazos vencidos e dispara alerta | Alta |
| Classificador de urgência | LLM classifica mensagens recebidas | Alta |
| Motor de tickets | Cria/atualiza/fecha tickets por thread Slack | Alta |
| Escrita Notion (tarefas) | Cria tarefas no Notion a partir do Slack | Média |
| Leitor checkbox Notion | Lê campo "Gerar tarefas" e aciona fluxo | Média |
| Interface Slack (confirmar ✅) | Recebe confirmação de tarefa criada | Média |
| Agendador de prazos | Registra e monitora deadlines mencionados | Média |
| Reconciliação Whapi | Verifica consistência de mensagens entregues | Baixa |
| Backup SQLite | Backup automático dos bancos de dados | Baixa |
| Monitor externo (UptimeRobot) | Alerta de indisponibilidade da VPS | Alta (config, não código) |

**Relatórios e Consulta/Atualização** (apareceram na primeira mensagem, sumiram da lista):
- Relatórios: já existem (`relatorio/`). Precisam ser mantidos na lista oficial.
- Consulta/Atualização: é o engine atual (`agent/engine.py`). Também já existe.

---

## 8. O que precisa ser atualizado no documento técnico atual

O documento `⚙️ Agente de Performance — Documentação Técnica` (Notion) ainda descreve:

- ❌ "Dois agentes" — **Realidade:** processo único, `main.py`
- ❌ "SQLite entre processos" — **Realidade:** SQLite local federado via DuckDB no mesmo processo
- ❌ "Projeto `transcricao-510015`" — **Realidade:** pipeline consolidado em `guara_performance`
- ❌ POD Kombucha Google Ads como "em configuração" — **Realidade:** ativo desde 06/10
- ❌ Parquet como formato analítico — **Realidade:** BigQuery é o destino analítico
- ❌ Dooki não mencionado — **Realidade:** sync ativo para POD Kombucha

---

## Próximos passos sugeridos (para validar com Vitor)

1. **Definir escopo v2**: quais dos módulos "faltam" entram no próximo ciclo?
2. **Prioridade imediata**: cron "sem resposta" + classificador urgência (impacto direto no cliente)
3. **BQ writer**: migrar de streaming+append para batch com MERGE (elimina duplicatas definitivamente)
4. **Tickets**: definir estrutura de dados e regra de fechamento antes de implementar
5. **Monitor externo**: configurar UptimeRobot (15 minutos, sem código)
6. **Atualizar doc técnico** no Notion após validação aqui

---

*Gerado em 06/10/2026. Para revisão com Vitor — não é versão final.*
