# Ficha do card BigQuery Guará: seções 2, 8 e 10

Rascunho para revisão antes de ir para o card no Notion.

## 2. MVP

A carga diária completa de Meta Ads, Google Ads e Shopify de todos os clientes ativos
(Source Of, POD Kombucha e Botica), rodando na VM Oracle, com nova tentativa automática e alerta
de falha no #alertas-tecnicos. O Agente de Performance lê os números do BigQuery (camada gold)
em vez do banco local do notebook.

Fica fora do MVP: AprovaPost, Central de leads, Referenciador de ads e o registro de carga por
cliente, fonte e dia (item 10), que entram nas semanas seguintes.

## 10. Métricas de aceite

Medidas por 4 semanas seguidas depois da virada para a VM:

| Métrica | Meta | Como medir |
|---|---|---|
| Dias com carga completa, por cliente e fonte | 98% ou mais | dias com dado de Meta, Google e Shopify para cada cliente ativo, sobre o total de dias |
| Divergência entre o banco e a API | abaixo de 2% | investimento, pedidos e receita do BigQuery comparados com o painel do Meta, do Google e da Shopify, por cliente e fonte, numa amostra de dias por semana |
| Carga parada sem alerta | nenhuma | toda lacuna de mais de 4h entre cargas do gold tem um alerta correspondente no #alertas-tecnicos |

## 8. Teto de custo

R$ 50 por mês para o projeto `guara-db`, com alerta de orçamento do Google Cloud em 50%, 80% e
100%.

Base: a projeção de outubro dá US$ 0 por mês com a carga corrigida e cerca de US$ 25 por mês sem a correção, com 4 contas. O teto cobre a carga corrigida
com folga e avisa antes de chegar ao custo da carga sem correção.
