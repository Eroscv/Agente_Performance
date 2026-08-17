# Olá. Eu sou o Agente de Performance Guará. ⚙️

Sou um analista de mídia paga com inteligência artificial, criado para a **Guará Marketing**. Trabalho todos os dias para transformar dados de campanhas em decisões concretas — sem planilhas, sem trabalho manual, sem esperar ninguém.

---

## O que eu faço

Monitoro, analiso e reporto a performance de campanhas publicitárias no **Meta Ads** (Facebook/Instagram) e **Google Ads** para os clientes da Guará. Funciono 24 horas por dia, 7 dias por semana.

Você me faz uma pergunta no Slack, eu respondo em segundos. Ou então, toda manhã, você já acorda com o relatório pronto.

---

## Como falar comigo

Me mencione no Slack ou me mande uma mensagem direta. Falo português.

```
@agente como foi o ROAS essa semana?
@agente qual campanha está com pior performance?
@agente compare essa semana com a anterior
@agente quanto investimos no Meta esse mês?
@agente quais campanhas estão sem conversão?
```

Também tenho comandos rápidos:

| Comando | O que faz |
|---|---|
| `/hoje` | Resumo do dia anterior |
| `/semana` | Análise da semana atual |
| `/mes` | Fechamento do mês |
| `/top roas` | Ranking de campanhas por ROAS |
| `/alertas` | Campanhas com performance abaixo do esperado |
| `/compare` | Comparativo de períodos |
| `/campanha [nome]` | Análise de uma campanha específica |
| `/relatorio semana` | Relatório completo semanal no Notion |
| `/ajuda` | Lista completa de comandos |

---

## O que entrego automaticamente

Todo dia de manhã, sem que ninguém precise pedir:

| Horário (BRT) | O que acontece |
|---|---|
| 06h00 | Sincronizo os dados da Shopify |
| 07h00 | Sincronizo os dados do Google Ads |
| 07h30 | Sincronizo os dados do Meta Ads |
| 09h00 | Envio o relatório diário no Slack + publico no Notion |
| 07h00 (seg) | Envio o relatório semanal completo + publico no Notion |
| 08h00 (dia 1) | Envio o relatório mensal completo + publico no Notion |

---

## O que tem nos meus relatórios

Cada relatório tem 6 seções:

1. **Resumo executivo** — os KPIs principais do período em uma tabela
2. **Análise comparativa** — como foi versus o período anterior
3. **Desempenho por campanha** — qual performou melhor e pior
4. **Métricas de site** — receita real da Shopify, pedidos, ticket médio
5. **Tendência** — projeção de fechamento do mês
6. **💡 Insights & Ações** — o que fazer agora, o que otimizar, onde tem oportunidade

Essa última seção é gerada por IA com base nos dados reais das campanhas. Não é texto genérico — são ações específicas com os nomes das campanhas e os números do período.

---

## O que já sei sobre os clientes

### 🔵 Source OF
- **Meta Ads:** monitorando desde janeiro/2024
- **32.138 registros** de campanhas por anúncio/dia
- **R$ 3.197.315** em investimento total acompanhado
- **R$ 10.742.910** em receita atribuída
- **R$ 10.564.483** em receita real Shopify (413 dias sincronizados)
- **Google Ads:** monitorando desde maio/2026

### 🟢 POD Kombucha
- **Meta Ads:** ativo
- **Google Ads:** em configuração

---

## Como funciono por dentro

```
Você pergunta no Slack
        ↓
Interpreto a intenção (NLP)
        ↓
Consulto o banco de dados local (SQLite)
        ↓
Gero a resposta em português (Claude IA)
        ↓
Posto no Slack em thread
        ↓
Publico no Notion (quando relevante)
        ↓
Salvo tudo no banco (consultas, relatórios, contexto)
```

Cada pergunta que você me faz fica salva. Cada relatório gerado fica registrado. Com o tempo, fico mais preciso nas respostas — especialmente nas que você aprova com 👍.

---

## O que aprendi a fazer sozinho

- Detectar quando os dados estão desatualizados e avisar
- Lembrar o contexto da conversa (se você pergunta "e a semana anterior?", eu sei o que você quer comparar)
- Identificar perguntas sobre Google Ads sem precisar de comando especial
- Sugerir o próximo passo depois de responder
- Usar respostas anteriores aprovadas para melhorar as próximas

---

## Minhas regras

- **Nunca invento dados.** Se não tenho, aviso.
- **Nunca misturo dados de clientes.** Source OF e POD Kombucha são universos separados.
- **Sou transparente sobre limitações.** Se o sync atrasou, digo. Se os dados têm ressalvas, explico.
- **ROAS real > ROAS reportado.** Uso a receita da Shopify dividida pelo investimento com imposto — não o número que o Meta mostra.

---

## Onde ficam os dados

Tudo local, no servidor da Guará. Banco SQLite em `data/campanhas.db`. Nenhum dado de clientes sai do servidor.

---

## Quem me criou

Fui desenvolvido por **Eros (Guará Marketing)** com ajuda do **Cesar**, agente de IA da OpenClaw. Sigo evoluindo conforme novos clientes e funcionalidades são adicionados.

Para adicionar um novo cliente: editar `clientes.json`. Sem mexer em código.

---

*Agente de Performance Guará — versão agosto/2026*
