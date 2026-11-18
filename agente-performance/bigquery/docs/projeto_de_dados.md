# Projeto de dados do BigQuery Guará (guara-db)

Rascunho para aprovação do revisor do card. Organiza o banco antes de qualquer tabela nova:
de onde vêm os dados, em que camada ficam, quem lê cada tabela e quem pode criar, escrever e
apagar. Levantamento feito no BigQuery e no código em 17/11/2026.

## 1. Fontes por cliente

| Cliente | Fonte | Bronze (`guara_performance`) | Situação em 17/11 |
|---|---|---|---|
| Source Of | Shopify: resumo do dia, pedidos com UTM e tipo de cliente, itens, checkouts abandonados | `shopify_diario`, `pedidos_diario`, `pedido_itens`, `abandoned_checkout_*` | em dia |
| Source Of | Meta Ads (por anúncio) e Google Ads (por campanha) | `ads_diario`, `meta_campaign_totals`, `novos_clientes_semanal` | em dia |
| POD Kombucha | Shopify pod.com.br: pedidos e primeiro pedido de cada cliente | `pedidos_diario`, `shopify_clientes_historico`, `shopify_diario` | em dia |
| POD Kombucha | Yampi: lojas poddi, pod-kombucha e pod8 | `dooki_pedidos` | em dia (pod8 sem pedidos) |
| POD Kombucha | Meta Ads e Google Ads | `ads_diario` | em dia (Meta zerado em 05 e 06/11) |
| Botica | Shopify | `shopify_diario` | em dia (loja com poucas vendas: 4 pedidos desde 15/10) |
| Botica | Meta Ads (sem Google) | `ads_diario` | em dia desde 17/11 (token permanente; recarregado desde 30/10) |
| Interno | Pipeline de transcrição de reuniões, tickets e SLA | `analise_reunioes`, `tickets_alerta`, `metricas_sla`, `transcricao` | em dia |

Duas cargas gravam o bronze hoje: a do notebook (conta `agente-guara-bq`, branch do PR 6) e o ciclo de 3h do Agente de Performance na VM (conta `guara-vps-feq`, branch `migracao-vps-feq`, desde 12/11). A da VM usa o loader antigo da Meta, que grava todos os cliques como cliques no link: os dias que ela grava por último ficam sem cliques no link na camada tratada. As duas precisam rodar o mesmo código, ou só uma delas gravar.

Fora do escopo de performance, no mesmo projeto: `eco1100.leads_eco_1100` (leads de uma LP),
`plataforma_aprendizado_videos.videos` e o dataset `transcricao`.

## 2. Camadas

| Camada | Dataset | O que guarda | Quem escreve |
|---|---|---|---|
| Bruto (bronze) | `guara_performance` | dado como veio da API, uma linha por pedido, anúncio ou dia | scripts de carga (`cron_master_sync.sh`) |
| Tratado (silver) | `ingestao` | o bronze limpo, sem duplicata, com tipos certos, por MERGE | procedure `ingestao.load_silver` |
| Pronto para uso (gold) | `correlacao` | fatos e dimensões que os apps leem | procedure `correlacao.load_gold` |

Regras:
- Um app lê só o gold, ou views de consumo feitas sobre o gold. Nenhum app lê o bronze.
- O gold é recalculável: pode ser apagado e refeito a partir da silver sem perder nada.
- Toda carga grava uma linha em `ingestao.log_cargas` (já existe para a silver: camada, tabela,
  linhas da fonte e linhas afetadas). O item 10 do card estende isso para cliente, fonte, dia e
  situação.

## 3. Tabelas brutas que nenhuma camada lê hoje

| Tabela | Quem grava | Proposta |
|---|---|---|
| `raw_meta_insights`, `raw_google_ads`, `raw_shopify_pedidos`, `raw_yampi_pedidos` | os loaders, com a resposta da API inteira | manter como auditoria (permite refazer o bronze sem chamar a API). Sem expiração por enquanto: as tabelas não têm partição e somam 213 MB em 17/11 (custo de armazenamento abaixo de US$ 0,01 por mês). Rever quando passarem de 10 GB |
| `dooki_pedidos_diario` | ninguém desde 05/11 (substituída por `dooki_pedidos`) | nenhuma view lê mais (a `v_todos_dados` já sai de `ing_yampi_pedidos`), mas ainda é consultada à mão e mostra dado parado em 05/11. Trocar por uma view de mesmo nome sobre `ing_yampi_pedidos`, ou apagar depois de avisar quem consulta |
| Dataset `teste`: 11 tabelas e 25 views paradas desde 22/10 | ninguém | apagar depois de confirmar que nada lê (item 9 do card) |
| Cópias `_bkp_*` e `_pre_*` (20 tabelas, de 04 a 09/11) | snapshots das migrações de novembro | apagar a partir de 09/12, 30 dias depois da última |

## 4. Regra de canal da POD (as duas tabelas que divergem)

A POD tem duas tabelas de pedidos no gold, com regras de canal diferentes para o mesmo `utm_source`:

| | `correlacao.fato_pedidos` | `correlacao.fato_pedidos_pod` |
|---|---|---|
| Regra | lista fechada: `facebook`, `instagram`, `ig`, `igshopping`, `facebook ads`, `meta` → Meta; `google` exato → Google; vazio → Direto; resto → Outros | `fn_canal_pod` (regra do Colab da Bia): contém `facebook/meta/fb/ig/instagram` → Meta; contém `google` → Google; `revi/email/sms/shopper` → Outros; vazio ou desconhecido → Orgânico/Direto |
| Exemplo `klaviyo` | Outros | Orgânico/Direto |
| Exemplo `google_ads` | Outros | Google |

Correção proposta: a POD usa só `fn_canal_pod` nas duas tabelas (é a regra validada contra o
Colab). Source Of continua com a regra dela, que é a mesma do resumo diário da Shopify.
A regra de canal fica em uma função por cliente no gold, nunca repetida em consulta de app.

## 5. Quem cria, escreve e apaga

| Ação | Quem | Como |
|---|---|---|
| Criar ou mudar tabela, view ou procedure em produção | Eros, com revisão do Miguel | arquivo versionado em `bigquery/scripts/`, pull request revisado e `scripts/apply_bq_sql.py --revisor`, que registra em `correlacao.log_alteracoes_bq` |
| Escrever dados | só as contas de serviço das cargas (VM e GitHub Actions) | nenhuma pessoa escreve dado à mão |
| Ler | uma conta de serviço só de leitura por app (BigQuery Data Viewer no dataset que ele usa) | a chave fica na plataforma do app (Vercel, VM), nunca no código |
| Apagar tabela | Eros, com revisão | só depois de conferir em `INFORMATION_SCHEMA.JOBS` que nada leu a tabela em 30 dias |
| Testes | qualquer um | num dataset ou projeto de teste separado, nunca em `guara_performance`, `ingestao` ou `correlacao` |

## 6. Apps que vão consumir o banco

| App | Hoje | O que vai ler | Dataset |
|---|---|---|---|
| Agente de Performance | tenta o BigQuery primeiro, mas na camada bruta (`ads_diario`) com tradução de SQL por regex que perdia colunas e escondia erros; parte das consultas lia direto o banco local (SQLite/DuckDB). Em migração (PR 13): views de consumo `correlacao.v_agente_performance`, `v_agente_campanhas`, `v_agente_anuncios` e `v_agente_shopify_diario`, com as mesmas colunas das views locais | as views de consumo acima; depois `fato_performance_diaria` e `fato_cac` para os números de negócio | `correlacao` |
| Artefatos recorrentes (painéis) | painel ao vivo (PR 5) e API do painel da Source Of leem `fato_performance_diaria` e as tabelas `ing_ads_diario` e `ing_shopify_resumo_diario` da silver | as mesmas informações, por views de consumo no gold (`v_painel_campanhas`, `v_painel_loja`), para nenhum app ler a silver | `correlacao` |
| Referenciador de ads | carga testada em 18/11 no dataset `referencias_teste` (tabelas `marcas`, `anuncios`, `salvos`), já removido; produção depende do deploy na VM | cópia semanal das marcas acompanhadas, anúncios coletados e salvos | `referencias` (novo) |
| AprovaPost | carga diária testada em 18/11 no dataset `aprovapost_teste` (tabelas `criativos`, `versoes`, `decisoes`, `acessos_cliente`), já removido; produção depende do deploy na VM e do id da agência no Neon | criativos, versões, decisões, datas, cliente e responsável, em lote diário. Os arquivos ficam no Cloudflare R2 e o banco guarda só o caminho | `aprovapost` (novo) |
| Central de leads | ainda não existe no banco (só `eco1100.leads_eco_1100`, de uma LP) | leads por fonte, ver seção 7 | `leads` (novo) |
| Sistema de alertas técnicos | alerta de frescor (PR 3) lê a hora de atualização do gold | `alertas_qualidade_dados`, `qualidade_dados_checks`, `log_cargas`, `log_alteracoes_bq` e a hora da última carga de cada fonte | `correlacao`, `ingestao` |

As cargas do AprovaPost e do Referenciador usam a conta de serviço `aprovapost-carga` e ficam na região US, a mesma dos outros datasets.

## 7. Tabelas da Central de leads (primeira versão)

Entram o formulário da LP, o Meta (formulário de lead) e o WhatsApp. LinkedIn e ManyChat ficam
para depois. Mesmo desenho de camadas:

| Camada | Tabela | Campos |
|---|---|---|
| Bruto | `leads.raw_lp_formulario`, `leads.raw_meta_lead_ads`, `leads.raw_whatsapp` | a resposta de cada fonte como veio, com `recebido_em` |
| Tratado | `leads.ing_leads` | `lead_id`, `cliente`, `fonte` (lp, meta, whatsapp), `recebido_em`, `nome`, `email`, `telefone`, `utm_source`, `utm_campaign`, `campanha_id`, `formulario_id`, `mensagem` |
| Pronto para uso | `correlacao.fato_leads` | um lead por pessoa e cliente (sem duplicata por e-mail ou telefone), com a primeira fonte e a data |

No WhatsApp entram só os campos essenciais: telefone, nome do contato, data da primeira mensagem
e a campanha de origem, se houver. O texto das conversas não entra no banco.
E-mail e telefone são dados pessoais: o dataset `leads` tem acesso restrito à conta de serviço da
Central de leads e ao Eros.
