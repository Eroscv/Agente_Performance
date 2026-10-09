# Correção: Dados duplicados no BigQuery (09/10/2026)

## O que aconteceu

O BigQuery é o banco de dados analítico usado pelos relatórios e dashboards do Agente de Performance. Nele existe uma tabela chamada `ads_diario` que armazena o desempenho diário de cada anúncio (Meta Ads e Google Ads) de todos os clientes.

Todo dia, o sistema busca os dados mais recentes nas plataformas (Meta, Google) e grava na `ads_diario`. O problema é que a forma como essa gravação estava programada **sempre adicionava linhas novas, sem nunca apagar as antigas**. Ou seja, cada sincronização diária criava cópias extras dos mesmos anúncios.

### Exemplo real

No dia 01/10, a conta de Meta Ads da POD Kombucha tinha apenas **70 anúncios ativos** com 3 campanhas. Mas na tabela do BigQuery havia **13.440 linhas** para esse mesmo dia — isso porque o sistema havia sincronizado ~192 vezes desde que a tabela foi criada, e cada vez gravou as mesmas 70 linhas novamente.

O resultado prático: o investimento da Semana 40 (28/09 a 04/10) aparecia como **R$ 7,7 milhões** no BigQuery, quando o valor real era de **~R$ 40 mil**.

A Source OF tinha o mesmo problema, com ~192 cópias de cada linha.

---

## Como foi identificado

Ao consultar o BigQuery filtrando **apenas a linha mais recente por anúncio por dia** (usando `ROW_NUMBER` ordenado por data de criação), os números bateram exatamente com o relatório real. Isso confirmou que os dados estavam corretos — só estavam multiplicados.

---

## O que foi corrigido

### 1. Limpeza dos dados históricos
A tabela `ads_diario` foi reconstruída mantendo apenas o registro mais recente de cada anúncio por dia. Todos os clientes foram corrigidos:

| Cliente | Plataforma | Semana 40 — antes | Semana 40 — depois |
|---|---|---|---|
| POD Kombucha | Meta Ads | ~R$ 7,7 milhões | ~R$ 40 mil ✅ |
| Source OF | Meta Ads | ~R$ 1,4 milhão/dia | Valores reais ✅ |

### 2. View de segurança `v_ads_diario`
Foi criada uma **view permanente** no BigQuery chamada `v_ads_diario`. Ela sempre retorna automaticamente só a linha mais recente de cada anúncio por dia, independente de quantas cópias existam na tabela original. Dashboards e consultas podem apontar para ela como camada extra de proteção.

### 3. Correção definitiva na gravação
O código responsável por gravar os dados no BigQuery foi reescrito. Antes ele simplesmente **adicionava** linhas. Agora ele faz uma operação chamada **MERGE (upsert)**:

- Se o anúncio já existe para aquele dia → **atualiza** com os dados mais recentes
- Se é um registro novo → **insere** normalmente

Isso garante que o problema não se repita em nenhuma sincronização futura.

---

## Status atual

| Item | Status |
|---|---|
| Dados históricos limpos | ✅ Concluído |
| View `v_ads_diario` criada | ✅ Concluído |
| Gravação corrigida (MERGE) | ✅ Concluído |
| POD Kombucha — Meta Ads | ✅ Valores corretos |
| Source OF — Meta Ads | ✅ Valores corretos |
| POD Kombucha — Google Ads | 🔍 Divergência de R$3k sendo investigada no painel do Google Ads |

---

## Google Ads POD Kombucha

O Google Ads da POD já está conectado e enviando dados (MCC 612-367-2195, ligado em 07/10/2026). Há uma pequena divergência de ~R$3k entre o que aparece no BigQuery e o relatório do Google — está sendo verificado diretamente no painel do Google Ads. Pode ser diferença de atribuição de conversão ou janela de datas.

---

*Corrigido por Cesar (Agente de Performance Guará) em 09/10/2026.*
