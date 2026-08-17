# 📡 Como foi configurada a ingestão de dados — Meta Ads e Google Ads

> Explicação completa: o problema, o que foi feito, como foi feito e o resultado.

---

## O problema antes

Antes desse sistema existir, os dados de campanhas chegavam assim:

- Alguém exportava manualmente do Meta Ads ou Google Ads
- Colava numa planilha Google Sheets
- O agente de IA lia a planilha quando precisava gerar um relatório

**Problemas disso:**
- Se ninguém exportasse, os dados ficavam desatualizados
- Exportar planilha é trabalho manual e repetitivo
- Risco de erro humano (exportou o período errado, colou na aba errada)
- Dependência de uma pessoa para manter tudo funcionando
- Impossível escalar para mais clientes sem mais trabalho

---

## A solução: conectar direto na fonte

Criamos módulos que falam **diretamente com as APIs oficiais** do Meta e do Google. Não passa mais por planilha. Os dados vão da plataforma para o banco de dados automaticamente, todo dia.

```
Antes:  Meta Ads → exportação manual → Google Sheets → agente
Depois: Meta Ads → API → banco de dados → agente
```

---

## Meta Ads — o que foi feito

### 1. Acesso à API

O Meta disponibiliza uma API (Graph API v20.0) que permite buscar todos os dados de campanhas programaticamente. Para acessá-la, precisamos de um **token de acesso** — uma espécie de senha especial que o Meta gera.

Configuramos um **token de longa duração** (válido por ~60 dias, renovável) para a conta da Source OF:
- Conta: `act_1059884611898142`
- Token armazenado com segurança na variável de ambiente `META_ACCESS_TOKEN` no servidor
- O token **nunca vai para o repositório** (segurança)

### 2. O que buscamos

Para cada anúncio ativo, buscamos diariamente:

| O que é | Para que serve |
|---|---|
| Nome da campanha / conjunto / anúncio | Identificar o criativo |
| Data | Agrupar por dia |
| Investimento (gasto) | Quanto foi gasto |
| Impressões e alcance | Quantas pessoas viram |
| Cliques | Quantas pessoas clicaram |
| Compras e receita atribuída | Resultado real de vendas |
| ROAS (retorno sobre investimento) | Eficiência da campanha |
| CPM, frequência | Saúde da campanha |
| Adições ao carrinho, checkouts | Funil de conversão |
| Thruplays de vídeo | Engajamento com vídeos |

### 3. Como a coleta funciona

A API do Meta tem uma limitação: não recomenda buscar mais de 30 dias de uma vez por causa do volume de dados. Resolvemos isso dividindo em **lotes de 14 dias**:

```
Buscar 90 dias →
  Lote 1: dias 1 a 14
  Lote 2: dias 15 a 28
  Lote 3: dias 29 a 42
  ... e assim por diante
```

Entre cada lote, o sistema aguarda 1 segundo para não sobrecarregar a API. Se a API retornar erro de limite (429), aguarda 60 segundos e tenta de novo.

### 4. O cálculo do ROAS real

O Meta reporta o ROAS baseado na **receita atribuída internamente** — mas esse número não leva em conta os impostos que incidem sobre o investimento em mídia.

A Guará usa o **ROAS real**, calculado assim:

```
ROAS real = Receita Shopify ÷ (Investimento Meta × 1,1215)
```

O fator **1,1215** corrige o imposto sobre o investimento. Esse número vem de uma tabela de comparação histórica com os dados reais de faturamento da Shopify.

---

## Google Ads — o que foi feito

### 1. Acesso à API

O Google Ads usa um sistema de autenticação mais elaborado chamado **OAuth2**. Funciona assim:

1. Criamos um projeto no Google Cloud Console
2. Geramos credenciais de acesso (Client ID e Client Secret)
3. O responsável da conta autorizou o acesso — o sistema gerou um **refresh token permanente**
4. Com esse refresh token, o sistema consegue se autenticar automaticamente, para sempre, sem precisar de interação humana

**Variáveis configuradas no servidor:**
- `GOOGLE_ADS_DEVELOPER_TOKEN` — identificação do desenvolvedor na API
- `GOOGLE_ADS_CLIENT_ID` e `GOOGLE_ADS_CLIENT_SECRET` — credenciais do app
- `GOOGLE_ADS_REFRESH_TOKEN` — token permanente que renova o acesso automaticamente
- `GOOGLE_ADS_LOGIN_CUSTOMER_ID` — ID da conta gerenciadora (MCC)

Instalamos também o **SDK oficial do Google Ads para Python** (`google-ads`).

### 2. O que buscamos

O Google Ads usa uma linguagem própria chamada **GAQL** (parecida com SQL) para consultar dados. Nossa consulta busca por campanha, por dia:

| O que é | Para que serve |
|---|---|
| Nome e ID da campanha | Identificar a campanha |
| Tipo (Search, Display, Shopping…) | Classificar o tipo de anúncio |
| Status (ativo, pausado) | Saber o estado atual |
| Data | Agrupar por dia |
| Investimento | Quanto foi gasto (convertido de micros para R$) |
| Impressões e cliques | Volume e engajamento |
| Conversões e valor de conversão | Resultado real |
| CTR | Taxa de clique |
| CPC médio | Custo por clique |

**Detalhe técnico:** O Google retorna custos em **micros** (1 real = 1.000.000 micros). O sistema converte automaticamente para reais.

### 3. Como a coleta funciona

O Google Ads é mais simples que o Meta nesse aspecto — a API retorna todos os dados do período de uma vez, sem precisar de lotes. O sistema busca os últimos N dias e substitui os dados anteriores.

---

## Como os crons funcionam

Um **cron** é uma tarefa agendada que roda automaticamente no horário configurado, como um alarme programado no servidor.

Configuramos 3 crons de ingestão de dados:

### Todos os dias, de manhã:

```
06h00 UTC → Busca receita da Shopify (últimos 8 dias)
07h00 UTC → Busca dados do Google Ads (últimos 8 dias)
07h30 UTC → Busca dados do Meta Ads (últimos 8 dias)
```

**Por que 8 dias e não apenas ontem?**

O Meta Ads tem **atribuição retroativa** — uma venda de hoje pode ser atribuída a um anúncio de 7 dias atrás. Puxar os últimos 8 dias garante que os números de dias anteriores sejam sempre corrigidos automaticamente. Sem isso, os dados históricos ficariam desatualizados.

### Depois de 09h00:

```
09h00 UTC → Gera e envia relatório diário no Slack
10h00 BRT → Envia relatório para todos os clientes
```

Todos os crons rodam de forma independente e registram o resultado no banco de dados (sucesso, erro, quantas linhas foram inseridas).

---

## Onde os dados são armazenados

Tudo vai para um **banco de dados SQLite** local no servidor, em `data/campanhas.db`. Cada cliente tem suas próprias tabelas:

```
source_of_meta_ads_diario   ← Meta Ads da Source OF
source_of_gads_diario       ← Google Ads da Source OF
shopify_receita_diaria      ← Vendas Shopify
```

Os dados de um cliente **nunca se misturam** com os de outro.

---

## O resultado

### Meta Ads — Source OF

| Dado | Valor |
|---|---|
| Histórico sincronizado | Janeiro/2024 até hoje |
| Total de registros | 32.138 linhas (por anúncio/dia) |
| Campanhas únicas | 32 campanhas |
| Investimento total registrado | R$ 3.197.315,04 |
| Receita atribuída registrada | R$ 10.742.910,87 |
| ROAS médio implícito | ~3,36× |

### Google Ads — Source OF

| Dado | Valor |
|---|---|
| Histórico sincronizado | Maio/2026 até hoje |
| Total de registros | 378 linhas (por campanha/dia) |
| Campanhas únicas | 7 campanhas |
| Investimento total registrado | R$ 39.091,53 |

### O que mudou na prática

| Antes | Depois |
|---|---|
| Planilha atualizada manualmente | Banco de dados atualizado automaticamente todo dia |
| Risco de dado desatualizado | Dados sempre frescos (máximo 1 dia de atraso) |
| Atribuição retroativa ignorada | Últimos 8 dias re-sincronizados todo dia |
| Um cliente, muito trabalho | Vários clientes, zero trabalho extra |
| Para mais um cliente: mais uma planilha | Para mais um cliente: adicionar 10 linhas no arquivo de configuração |

---

## Como adicionar um novo cliente

1. Abrir o arquivo `clientes.json`
2. Adicionar o bloco do novo cliente com o ID da conta Meta e/ou Google Ads
3. Salvar — na próxima execução dos crons, os dados já começam a ser coletados automaticamente

Não é necessário alterar nenhum código.
