# 🤖 O que é e o que foi feito — Agente de Performance Guará

> Explicação em linguagem simples, sem jargão técnico.

---

## O problema que existia antes

A Guará Marketing gerencia campanhas de anúncios para vários clientes no Meta Ads (Facebook/Instagram) e Google Ads.

Todo dia, alguém precisava:
1. Entrar nas plataformas manualmente
2. Exportar os dados para planilhas Google Sheets
3. Organizar os números
4. Montar um relatório
5. Enviar para o cliente

Isso tomava tempo, era repetitivo e dependia de uma pessoa fazendo tudo certo toda vez.

---

## O que foi construído

Um **agente de inteligência artificial** que faz tudo isso automaticamente.

Pensa nele como um analista de marketing digital que:
- Acorda todo dia de manhã
- Vai buscar os dados de anúncios direto nas fontes (sem planilha)
- Organiza tudo num banco de dados próprio
- Gera relatórios prontos
- Publica no Notion e avisa no Slack
- Responde perguntas sobre performance em português, pelo Slack

---

## Como funciona no dia a dia

### De manhã (automático, sem ninguém precisar fazer nada):

| Horário | O que acontece |
|---|---|
| 06h00 | Busca os pedidos e receita da loja Shopify |
| 07h00 | Busca os dados de campanhas do Google Ads |
| 07h30 | Busca os dados de campanhas do Meta Ads (Facebook/Instagram) |
| 09h00 | Gera e envia o relatório diário no Slack |
| 10h00 | Envia resumo de performance para todos os clientes |

### Na segunda-feira:
- Gera o **relatório semanal** automaticamente

### No dia 1 de cada mês:
- Gera o **relatório mensal** completo com análise e recomendações

---

## O que o agente sabe responder

Pelo Slack, você pode perguntar coisas como:
- *"Qual foi o ROAS da Source OF essa semana?"*
- *"Compare a semana passada com essa semana no Google Ads"*
- *"Quais campanhas estão com performance abaixo do esperado?"*
- *"Quanto investimos no Meta esse mês?"*

O agente responde em segundos, em português, com os dados atualizados.

---

## O que mudou em relação ao antes

| Antes | Depois |
|---|---|
| Dados vinham de planilhas atualizadas manualmente | Dados vêm direto das APIs oficiais do Meta e Google |
| Relatório feito manualmente | Relatório gerado e publicado automaticamente |
| Alguém precisava lembrar de fazer todo dia | Tudo acontece sozinho toda manhã |
| Para saber um número, tinha que abrir planilha | Pergunta no Slack e recebe a resposta na hora |
| Um relatório por vez | Vários clientes atendidos ao mesmo tempo |

---

## Onde os dados ficam guardados

Todos os dados ficam num **banco de dados local** no servidor (SQLite). Cada cliente tem suas próprias tabelas separadas — os dados de um cliente nunca se misturam com os de outro.

O banco guarda o histórico completo:
- Campanhas Meta Ads por dia, por anúncio
- Campanhas Google Ads por dia
- Pedidos e receita Shopify — mais de **31 mil pedidos** e **R$ 9,5 milhões** já sincronizados

---

## Os relatórios

O agente gera 4 tipos de relatório:

### 📅 Diário
Resumo do dia anterior: quanto foi investido, quantas vendas vieram dos anúncios, ROAS.

### 📊 Semanal
Análise da semana: comparação com a semana anterior, quais campanhas performaram melhor, alertas.

### 📈 Mensal
Fechamento completo do mês: análise narrativa gerada por IA, atingimento de metas, tendências, próximos passos recomendados.

### 🔎 Por período
Relatório para qualquer data que você quiser.

Todos são publicados automaticamente no **Notion** (como páginas organizadas) e avisados no **Slack**.

---

## Clientes atendidos hoje

| Cliente | Meta Ads | Google Ads | Shopify |
|---|---|---|---|
| Source OF | ✅ Ativo | ✅ Ativo | ✅ Ativo |
| POD Kombucha | ✅ Ativo | 🔜 Em breve | — |

Para adicionar um novo cliente, basta editar um arquivo de configuração — sem precisar mexer no código.

---

## A inteligência por trás

O agente usa o **Claude** (IA da Anthropic, mesma empresa que faz Claude.ai) para:
- Gerar o texto narrativo dos relatórios
- Responder perguntas sobre performance
- Identificar tendências e recomendar ações

Ele segue as **regras de negócio da Guará** — por exemplo, o cálculo de ROAS real leva em conta os impostos sobre o investimento em mídia, não só o valor bruto reportado pelo Meta.

---

## Em resumo

Foi construído um sistema que transforma dados brutos de anúncios em informação útil, de forma automática, todo dia, para todos os clientes — sem depender de planilhas, sem trabalho manual repetitivo, e com a capacidade de responder perguntas em tempo real pelo Slack.
