# Yampi/Dooki: 3 lojas + carga por pedido (05/11/2026)

## Problema reportado

A Bia identificou que o BigQuery mostrava números de checkout da POD
Kombucha menores que a análise manual dela. Exemplo, 01/11:

| Fonte | Pedidos | Receita |
|---|---|---|
| BigQuery (antes) | 84 | R$25.171,72 |
| Análise manual (Bia) | 102 | R$30.537,00 |

## Causas encontradas

1. **Só 1 de 3 lojas Yampi era sincronizada.** A POD Kombucha vende em 3
   lojas Yampi/Dooki separadas (`poddi`, `pod-kombucha`, `pod8`), cada uma
   com API/token próprios. O `.env` tinha a variável `DOOKI_BASE_URL`
   duplicada — a segunda definição sobrescrevia a primeira — deixando a
   `poddi` sem nenhuma sincronização histórica e a `pod8` sem credencial
   configurada.

2. **Só pedidos com status `paid` eram contados.** Um pedido muda de
   status com o tempo (`paid` → `on_carriage` → `delivered`, ou →
   `cancelled`). Como a sincronização só olhava pra `paid` no momento
   exato em que rodava, qualquer pedido que já tivesse avançado de status
   antes do próximo sync desaparecia da receita **pra sempre**. Isso foi a
   causa raiz real do histórico da `poddi` estar praticamente vazio: ao
   rodar o primeiro backfill, os pedidos antigos já tinham saído de
   `paid`.

3. **Modelo de dados por total agregado do dia**, não por pedido — o que
   também causava bugs de duplicação/perda em reruns (uma sincronização
   incremental podia sobrescrever o total de um dia antigo com um valor
   parcial).

## Correções aplicadas

- Variáveis de ambiente renomeadas por loja: `DOOKI_PODDI_*`,
  `DOOKI_POD_KOMBUCHA_*`, `DOOKI_POD8_*`.
- `dooki/sync.py` reescrito do zero:
  - Sincroniza as 3 lojas em sequência.
  - Passou a contar como receita confirmada `paid` + `on_carriage` +
    `delivered` (antes só `paid`).
  - **Gravação por pedido** (não mais total por dia): tabela
    `guara-db.guara_performance.dooki_pedidos`, chave `(loja, order_id)`,
    upsert via `MERGE` — cada pedido é atualizado no lugar quando o
    status muda, nunca mais "perde" o dia.
  - Cada linha traz: `data`, `status`, `customer_id`, `novo_cliente`
    (calculado em batch, mesmo critério do Shopify: primeiro `order_id`
    daquele `customer_id` na loja), `utm_source`, `utm_medium`,
    `utm_campaign`, `utm_content` (= ID do anúncio, mesmo padrão do
    Shopify), `utm_term`, `valor_pago`, `desconto`, `frete`.
- `correlacao.load_gold` atualizada pra ler de `dooki_pedidos` (elimina a
  lógica de dedup/alias que o modelo antigo por total-do-dia exigia).
- Adicionado o sync ao `cron_master_sync.sh` (nunca tinha sido incluído
  lá).

## Backfill e validação

Backfill de 90 dias rodado nas 3 lojas:

| Loja | Pedidos encontrados (90 dias) |
|---|---|
| poddi | 158 |
| pod-kombucha | 7.151 |
| pod8 | 0 (loja sem nenhum pedido no período — API responde normalmente, só está vazia) |

Resultado pro dia 01/11 após as correções:

| Status | Loja | Pedidos | Receita |
|---|---|---|---|
| on_carriage | pod-kombucha | 81 | R$24.259,18 |
| delivered | pod-kombucha | 3 | R$912,54 |
| cancelled | pod-kombucha | 4 | R$903,05 |
| on_carriage | poddi | 12 | R$5.138,66 |
| cancelled | poddi | 10 | R$4.358,87 |

**Total confirmado (excluindo cancelados): 96 pedidos / R$30.310,38**
(contra 84/R$25.171,72 antes da correção).

## Resíduo em aberto — precisa da Bia

Ainda falta um resíduo de **6 pedidos / R$226,62** pra bater com os 102
pedidos / R$30.537,00 da análise manual.

Investigação do resíduo:
- Descartada hipótese de fuso horário — a API da Dooki já devolve as
  datas no fuso `America/Sao_Paulo`, não UTC.
- Contagem completa de **todos os status** (incluindo cancelados) pra
  01/11 nas 2 lojas ativas dá **110 pedidos no total**. O número da Bia
  (102) fica entre "só confirmados" (96) e "todos, incluindo cancelados"
  (110) — sugerindo que parte dos 14 pedidos cancelados deveria contar.
- Verificado se a API tem algum campo que diferencie "cancelado sem
  pagar" de "cancelado após capturar o pagamento": o campo `has_payment`
  aparece `True` em todos os 14 cancelados de forma indistinta — não é
  um sinal confiável.

**O que precisamos da Bia:** olhar o painel financeiro da própria Yampi
(não a API de pedidos) pros 14 pedidos cancelados de 01/11 (10 na poddi,
4 na pod-kombucha) e confirmar quais deles (se algum) tiveram o
pagamento capturado antes do cancelamento/reembolso. Com essa lista,
ajustamos a regra final de forma que valha pra todos os dias, não só
esse.

## Commits

- `400ab26` — sincroniza as 3 lojas Yampi/Dooki (poddi, pod_kombucha, pod8)
- `2221da5` — conta on_carriage/delivered como receita confirmada
- `2ed49f6` — carga por pedido em vez de total agregado por dia
