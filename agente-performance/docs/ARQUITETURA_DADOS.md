# 🗄️ Arquitetura de Dados — Agente de Performance Guará

> **Como os dados de campanhas chegam, onde ficam, como são consultados — e o que isso custa.**

---

## O que foi feito

O Agente de Performance coleta dados de **Meta Ads**, **Google Ads** e **Shopify** e os armazena em duas camadas:

1. **SQLite local** — banco operacional. Rápido, sem custo, roda no próprio servidor. Guarda o histórico de campanhas por cliente, logs de sync, cache de respostas.

2. **BigQuery (Google Cloud)** — espelho analítico. Toda vez que um dado é salvo no SQLite, ele é automaticamente copiado para o BQ em paralelo. Essa cópia é chamada de **dual-write**.

O BigQuery não é a fonte primária — é uma camada de leitura para dashboards (Looker Studio), análises SQL ad-hoc e relatórios cross-cliente. Se o BQ estiver fora do ar ou der erro, o sistema continua funcionando normalmente; a falha é logada silenciosamente.

### O que existe hoje no BigQuery

| Tabela | Conteúdo | Linhas atuais |
|---|---|---|
| `ads_diario` | Meta Ads + Google Ads de todos os clientes, por anúncio/campanha/dia | ~22.700 |
| `shopify_diario` | Receita diária Shopify por cliente | ~1.577 |
| `metricas_sla` | SLA de atendimento da Flora | ~564 |
| `analise_reunioes` | Resumos de reuniões (Google Drive) | ~28 |
| `tickets_alerta` | Alertas de campanhas abaixo do threshold | ~29 |

Além das tabelas, existem **views** que materializam cálculos frequentes:

| View | O que calcula |
|---|---|
| `v_performance_mensal` | ROAS real, CPA, variação MoM e YoY cruzando ads + Shopify |
| `v_ads_diario` | Última versão de cada anúncio por dia (deduplicação de segurança) |
| `v_benchmark_mensal` | Benchmarks por cliente e plataforma |

---

## Como foi feito

### Dual-write: escrita paralela SQLite + BigQuery

Toda sincronização segue este fluxo:

```
API Externa (Meta / Google / Shopify)
        │
        ▼
   loader.py — salva no SQLite (primário, síncrono)
        │
        ▼ (silencioso — não bloqueia se falhar)
   bq_writer.py — espelha no BigQuery (analítico, assíncrono)
```

O módulo `db/bq_writer.py` tem três funções públicas:

- `write_ads(rows, cliente, plataforma)` — espelha campanhas Meta ou Google
- `write_shopify(rows, cliente)` — espelha receita Shopify
- `enabled()` — retorna `True` se as credenciais GCP estão presentes

### MERGE (upsert) — por que não é um INSERT simples

O sync roda a cada 15 minutos. Sem controle de duplicatas, a mesma campanha seria inserida 96 vezes por dia.

A solução é um **MERGE** (upsert): para cada lote de dados, o sistema:

1. Cria uma tabela temporária no BQ com os dados novos
2. Executa um `MERGE INTO ads_diario`: atualiza se a chave já existe, insere se não existe
3. Deleta a tabela temporária

A **chave de merge** varia por plataforma:

| Plataforma | Chave única |
|---|---|
| **Meta Ads** | `cliente + plataforma + data + campaign_id + adset_id + ad_id` |
| **Google Ads** | `cliente + plataforma + data + campaign_id` |
| **Shopify** | `cliente + data` |

> **Por que chaves diferentes?**
> O Meta Ads rastreia performance no nível de **anúncio individual** (`ad_id`). O Google Ads reporta no nível de **campanha** — `ad_id` e `adset_id` chegam sempre como `NULL`. Usar a mesma chave para os dois faz o MERGE falhar silenciosamente (em SQL, `NULL = NULL` retorna `FALSE`), e cada sync se tornaria um INSERT novo.

### Comparação NULL-safe no MERGE

O `ON` clause usa `IS NOT DISTINCT FROM` em vez de `=`:

```sql
-- ❌ Quebrado: NULL = NULL → FALSE → sempre insere nova linha
ON T.ad_id = S.ad_id

-- ✅ Correto: NULL IS NOT DISTINCT FROM NULL → TRUE → faz match
ON (T.ad_id IS NOT DISTINCT FROM S.ad_id)
```

### Particionamento e clustering

A tabela `ads_diario` foi criada com:

- **Particionamento por `data`** (coluna DATE) — o BQ só lê a partição do dia consultado, em vez de varrer a tabela inteira
- **Clustering por `cliente, plataforma`** — dentro de cada partição, os dados são ordenados por cliente e plataforma, acelerando filtros

---

## Por que foi feito desse jeito

### Por que dual-write e não ETL periódico?

ETL periódico (ex: rodar um script às 23h e copiar tudo) teria latência de até 24h. Com dual-write, o BQ fica atualizado em até 15 minutos após qualquer sync — o mesmo delay dos dados no SQLite. Dashboards em Looker Studio sempre mostram dados do dia.

### Por que BigQuery e não PostgreSQL/MySQL/Supabase?

| Critério | BigQuery | Postgres/Supabase |
|---|---|---|
| Custo em volume baixo | Gratuito até 10GB armazenados e 1TB/mês de queries | Cobrança mensal por instância (~$25/mês) |
| Escala | Petabytes sem configuração | Precisa de provisionamento |
| SQL analítico (window functions, partições) | Nativo e otimizado | Disponível, mas mais lento em tabelas grandes |
| Looker Studio | Integração nativa | Precisa de conector externo |
| Manutenção | Zero — serverless | Backup, patch, uptime para gerenciar |

Para o volume atual (~25k linhas no total), o custo é zero. Para múltiplos clientes ao longo de anos, escala sem re-arquitetura.

### Por que SQLite como primário e não só o BigQuery?

O BigQuery é bom para leitura analítica, não para escrita transacional frequente. Cada write no BQ cobra por bytes processados no MERGE. Usar o BQ como banco operacional com 96 merges/dia por cliente sairia caro e seria lento. O SQLite é instantâneo, local, e não tem custo.

### Por que o sync roda a cada 15 minutos?

As APIs de Meta e Google atualizam os dados com delay — uma compra registrada agora pode aparecer na API em 10-30 minutos. Sync a cada 15 minutos garante que os dados no sistema estejam sempre dentro de uma janela de ~30 minutos de defasagem em relação à realidade.

---

## Consequências: custo, crescimento e o que monitorar

### Custo atual

O projeto usa o projeto GCP `guara-db`, dataset `guara_performance`.

| Categoria | Regime atual | Custo atual |
|---|---|---|
| **Armazenamento** | ~8 MB ativos | **Gratuito** (limite: 10 GB) |
| **Queries (MERGE)** | ~96 merges/dia × ~500KB processados | **Gratuito** (limite: 1 TB/mês) |
| **Queries (leitura/dashboard)** | Consultas manuais e Looker Studio | **Gratuito** (dentro do 1 TB) |
| **Tabelas temporárias** | Criadas e deletadas no MERGE | **Gratuito** (TTL 1 dia) |

**Custo mensal estimado hoje: R$ 0.**

O BigQuery é [serverless no modelo de preço on-demand](https://cloud.google.com/bigquery/pricing): você paga por TB de dados varridos em queries, não por tempo de execução. O armazenamento dos primeiros 10 GB é gratuito permanentemente; as primeiras 1 TB/mês de queries também.

### Custo se crescer

**Cenário 1 — Mais clientes (2–5 clientes novos):**

Cada cliente ativo gera ~150-200 linhas de ads por dia (Meta + Google). Com 5 clientes: ~1.000 linhas/dia → ~365.000 linhas/ano. Em 5 anos = ~1,8 milhão de linhas. Tamanho estimado: ~600 MB. Ainda dentro da faixa gratuita.

**Cenário 2 — Dashboards com consultas pesadas (sem partição):**

Uma query que varre `ads_diario` sem filtro de data processaria o dataset inteiro. Com 1 GB de dados e 100 execuções/mês: 100 GB processados = **$0,50/mês** (BQ cobra $5/TB). Com uso de particionamento por data (já configurado), uma query filtrada por semana lê apenas ~7/365 = ~2% dos dados.

**Cenário 3 — 10+ clientes com histórico de 3 anos:**

~50 GB de armazenamento. Acima de 10 GB: **$0,02/GB/mês** → ~$0,80/mês de armazenamento. Queries com filtro de data: ainda baratas pelo particionamento.

**Conclusão:** o custo só se torna relevante acima de 10+ clientes ativos com 3+ anos de histórico ou com queries sem filtro de data rodando em loop. Abaixo disso: gratuito.

### O que precisa ser acompanhado

#### 🔴 Crítico — verificar semanalmente

**Duplicatas na `ads_diario`:**
A tabela já sofreu dois episódios de duplicação (09/10 e 13/10/2026). O MERGE pode quebrar silenciosamente se houver mudança de schema, falha de credencial, ou novo cliente com campos `NULL` inesperados.

```sql
-- Cole no BigQuery console — deve retornar todas as linhas com ratio = 1.0
SELECT data, COUNT(*) as linhas,
  COUNT(DISTINCT CONCAT(cliente,'_',plataforma,'_',
    COALESCE(campaign_id,'_'),'_',COALESCE(adset_id,'_'),'_',COALESCE(ad_id,'_'))) as unicos,
  ROUND(COUNT(*) / COUNT(DISTINCT ...), 1) as ratio
FROM `guara-db.guara_performance.ads_diario`
WHERE data >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
GROUP BY data ORDER BY data DESC;
```

Se `ratio > 1.0` em qualquer dia → rodar o script de dedup (`alteracoes/2026-10-13_bigquery-dedup-fix-merge-chave-plataforma.md`).

#### 🟡 Importante — verificar mensalmente

**Tabelas temporárias órfãs:**
O MERGE cria uma tabela `_ads_diario_tmp_YYYYMMDD...` e a deleta no final. Se o processo travar no meio, a tabela fica acumulando. Verificar no GCP Console > BigQuery > dataset `guara_performance` se há tabelas com prefixo `_` com mais de 1 dia de idade.

**Crescimento do dataset:**
```sql
SELECT table_id, row_count, ROUND(size_bytes/1024/1024, 1) as mb
FROM `guara-db.guara_performance.__TABLES__`
ORDER BY size_bytes DESC;
```
Crescimento saudável: ~150-200 linhas/dia por cliente em `ads_diario`. Pico acima de 500 linhas/dia indica sync duplicado.

**Quota de queries BQ:**
No GCP Console > BigQuery > Monitoring — verificar `Bytes processed per day`. Se se aproximar de 800 GB/mês, revisar queries sem filtro de data.

#### 🟢 Rotina — verificar a qualquer momento

**Verificar se dual-write está ativo:**
```bash
cd /home/ubuntu/.openclaw/workspace/agente-performance
/home/ubuntu/.openclaw/workspace/agente-performance/venv/bin/python3 -c "
import sys, os; sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from db.bq_writer import enabled
print('BQ ativo:', enabled())
"
```

**Verificar se o MERGE está funcionando:**
Comparar o número de linhas em `ads_diario` para a data de ontem com o número de anúncios ativos no Meta/Google. Devem ser aproximadamente iguais (ratio 1:1).

**Log do agente para erros BQ:**
```bash
journalctl -u agente-guara.service --since "today" | grep "\[BQ\]"
# ✅ esperado: [BQ] ✅ source_of/meta — MERGE 170 linhas OK
# ❌ alerta:   [BQ] source_of/meta — falha silenciosa: ...
```

---

## Histórico de incidentes e correções

| Data | Problema | Causa | Fix |
|---|---|---|---|
| 09/10/2026 | 192x duplicação em `ads_diario` | `insert_rows_json` sem upsert — cada sync adicionava linhas em vez de atualizar | Reescrita com MERGE via tabela temporária + view `v_ads_diario` |
| 13/10/2026 | 8–12x duplicação após o fix de 09/10 | Chave MERGE usava `ad_id` (NULL no Google Ads) + `NULL = NULL = FALSE` no SQL | Chave dinâmica por plataforma + `IS NOT DISTINCT FROM` no ON clause |

---

## Referências

- `db/bq_writer.py` — módulo de dual-write
- `alteracoes/2026-10-09_bigquery-deduplicacao.md` — primeiro incidente de duplicação
- `alteracoes/2026-10-13_bigquery-dedup-fix-merge-chave-plataforma.md` — segundo fix
- `docs/BIGQUERY.md` — documentação de configuração e queries úteis
- [BigQuery pricing](https://cloud.google.com/bigquery/pricing) — referência oficial de preços
