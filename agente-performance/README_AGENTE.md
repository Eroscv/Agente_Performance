# 📊 Agente de Performance — Guia de Uso

Consulte métricas de campanhas Meta Ads (Source OF) e Google Ads (POD Kombucha) direto pelo Slack, sem precisar abrir planilhas ou dashboards.

---

## Como usar

Você pode interagir de **duas formas**:

1. **Slash commands** — respostas diretas e rápidas
2. **Pergunta em linguagem natural** — mencione o bot com `@agente` e pergunte livremente

---

## Slash commands disponíveis

| Comando | O que faz |
|---|---|
| `/hoje` | Resumo do dia atual |
| `/semana` | Resumo dos últimos 7 dias |
| `/mes [mês]` | Resumo do mês (ou de um mês específico) |
| `/top [N] [métrica]` | Top N campanhas por ROAS ou compras |
| `/campanha [nome]` | Performance de uma campanha específica |
| `/alertas` | Campanhas com ROAS ruim ou sem conversão |
| `/funil [campanha]` | Funil de conversão geral ou por campanha |
| `/compare <A> vs <B>` | Comparação direta entre duas campanhas |
| `/relatorio [opções]` | Gera relatório e publica no Notion |
| `/pod [consulta]` | Dados do POD Kombucha (Google Ads) |
| `/campanhas` | Lista todas as campanhas cadastradas |
| `/metricas` | Lista todas as métricas disponíveis |
| `/exemplos` | Exemplos de perguntas em linguagem natural |
| `/status` | Status do sistema (banco, cache, KB) |
| `/kb` | Últimas entradas da Base de Conhecimento |
| `/cache-limpar` | Limpa o cache de respostas |
| `/ajuda` | Lista resumida de comandos |
| `/tutorial` | Mostra este guia |

---

## Exemplos de uso

### Resumos por período

```
/hoje
/semana
/mes
/mes junho
/mes 2026-05
```

### Por campanha

```
/campanha ABO
/campanha Advantage Plus
/compare ABO vs Advantage
/top 5
/top 5 compras
/top 3 roas
```

### Alertas e funil

```
/alertas
/funil
/funil ABO
```

### Relatórios

```
/relatorio                        → semanal Source OF (semana passada)
/relatorio mes                    → mensal Source OF
/relatorio pod                    → semanal POD Kombucha
/relatorio pod mes                → mensal POD Kombucha
/relatorio 2026-06-01 2026-06-30  → período customizado
```

O relatório é gerado e publicado automaticamente no Notion.

---

## Perguntas em linguagem natural

Mencione `@agente` e escreva como falaria com um analista:

```
@agente como foi a semana passada?
@agente qual campanha teve melhor ROAS em junho?
@agente compare ABO vs Advantage esse mês
@agente tem alguma campanha sem conversão nos últimos 7 dias?
@agente resumo de maio vs junho
@agente quantas compras fizemos hoje?
@agente qual o custo por compra da Advantage?
```

> 💡 O agente entende contexto — você não precisa ser técnico. Pergunte como perguntaria para um colega.

---

## Métricas disponíveis

### Investimento & Receita
| Métrica | Descrição |
|---|---|
| `investimento` | Valor gasto (R$) |
| `receita` / `valor_compras` | Receita gerada (R$) |
| `roas` | Receita ÷ Investimento |
| `custo_por_compra` | CPO — R$ por compra |

### Conversões
| Métrica | Descrição |
|---|---|
| `compras` | Número de compras |
| `adicoes_carrinho` | Adds ao carrinho |
| `checkout_iniciado` | Checkouts iniciados |
| `taxa_conversao` | % cliques → compra |

### Alcance & Visibilidade
| Métrica | Descrição |
|---|---|
| `impressoes` | Impressões totais |
| `alcance` | Pessoas únicas alcançadas |
| `frequencia` | Média de exposições (> 4 = fadiga) |
| `cpm` | Custo por mil impressões (R$) |

### Engajamento
| Métrica | Descrição |
|---|---|
| `cliques_link` | Cliques no link |
| `ctr` | Taxa de cliques (%) |
| `cpc` | Custo por clique (R$) |
| `visualizacoes_pagina` | Visualizações da landing page |

---

## POD Kombucha — Google Ads

O comando `/pod` acessa os dados do Google Ads do POD Kombucha:

```
/pod                   → como foi essa semana?
/pod status            → KPIs do dia mais recente (resposta instantânea)
/pod essa semana       → semana atual
/pod mes               → mês atual
/pod qual campanha está com melhor CTR?
```

**`/pod status`** retorna uma tabela rápida sem precisar do Claude:
```
📊 POD Kombucha · Status — Terça 28/10/2026
Investimento  │ R$ 842,30
Receita       │ R$ 3.201,00
ROAS          │ 3.80x
Conversões    │ 12,0
CTR           │ 2,41%
```

---

## Filtros de período aceitos

| O que digitar | Período |
|---|---|
| `hoje` | Dia atual |
| `ontem` | Dia anterior |
| `essa semana` | Segunda até hoje |
| `semana passada` | Segunda a domingo anterior |
| `esse mês` | Mês atual |
| `mês passado` | Mês anterior |
| `últimos 7 dias` | Últimos 7 dias corridos |
| `últimos 30 dias` | Últimos 30 dias corridos |
| `em junho` | Mês de junho do ano atual |
| `em 2025` | Ano inteiro |
| `01/06/2026` | Dia específico |
| `2026-06-01 2026-06-30` | Período customizado (só no `/relatorio`) |

---

## Base de Conhecimento

O agente aprende com o uso. Quando receber uma resposta boa, dê 👍 para salvá-la.

```
/kb              → lista as últimas entradas salvas
```

Respostas aprovadas são priorizadas automaticamente em consultas futuras similares — o agente fica mais preciso quanto mais você usa.

---

## Dicas

- **`/exemplos`** mostra um guia rápido de perguntas prontas — bom para consultar quando não sabe como formatar a pergunta
- **`/metricas`** lista todas as métricas com explicação — útil para saber o nome certo antes de perguntar
- **`/campanhas`** lista todas as campanhas com investimento acumulado — use antes do `/campanha` se não lembrar o nome exato
- **`/cache-limpar`** força busca nova nos dados brutos — use quando suspeitar que a resposta está desatualizada
- Todos os horários e datas são exibidos em **BRT (horário de Brasília)**
