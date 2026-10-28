# 🗂️ Agente de Cards — Guia de Uso

Crie e acompanhe cards no Notion direto pelo Slack, sem precisar abrir o Notion.

---

## Comandos disponíveis

| Comando | O que faz |
|---|---|
| `/criar-card` | Inicia o fluxo de criação de um novo card |
| `/status-cards [nome] [filtro]` | Consulta cards em aberto |
| `/meus-cards [N]` | Lista os últimos N cards que você criou |
| `/editar-card [título]` | Edita prazo, responsável, revisor ou descrição de um card |
| `/tutorial-transcricao` | Mostra este guia |

---

## /criar-card — passo a passo

O agente faz **8 perguntas** em sequência. Responda cada uma normalmente.

```
/criar-card
```

**Passo 1 — Cliente**
Digite o nome ou número:
- `1` Source Of
- `2` POD
- `3` Botica
- `4` Liv Up
- `5` Bazar
- `6` Todos

**Passo 2 — Frente**
- `1` ADS
- `2` Social
- `3` Conta
- `4` Relatoria
- `5` Planejamento
- `6` Site
- `7` Dados
- `8` Gestão

**Passo 3 — Entrega**
Descreva a tarefa com um verbo no infinitivo.
> Ex: `Criar campanha de Black Friday`

O título será gerado no formato: **CLIENTE: FRENTE - Entrega**
> Ex: `Source Of: ADS - Criar campanha de Black Friday`

**Passo 4 — Responsável**
Digite o número ou parte do nome:
- `1` Aline Satur
- `2` Bianca Garbin
- `3` Eros Vital
- `4` Guilherme Oliveira
- `5` Isabela Rodrigues
- `6` Matheus Ferreira
- `7` Miguel Ticianeli

**Passo 5 — Revisor**
Mesmo formato do responsável. ⚠️ Deve ser uma pessoa diferente do responsável.

**Passo 6 — Prazo**
Formatos aceitos:
| O que digitar | Resultado |
|---|---|
| `2026-11-15` | 15 de novembro de 2026 |
| `15/11/2026` | 15 de novembro de 2026 |
| `15/11` | 15 de novembro (ano atual) |
| `amanhã` | Dia seguinte |
| `hoje` | Hoje |
| `+7` | Em 7 dias |
| `pular` | +7 dias a partir de hoje (padrão) |

**Passo 7 — Descrição** _(opcional)_
Detalhe o que precisa ser feito, ou digite `pular`.

**Passo 8 — Confirmação**
Revise o resumo e confirme com:
- `confirmar` — cria o card no Notion ✅
- `cancelar` — descarta tudo ❌

---

## /status-cards — consultas

```
/status-cards
```
Sem argumento: mostra a lista de responsáveis disponíveis.

```
/status-cards Bianca
```
Todos os cards abertos da Bianca.

```
/status-cards Bianca em andamento
```
Só os cards em andamento da Bianca.

```
/status-cards todos bloqueado
```
Todos os cards bloqueados de todos os responsáveis.

**Status disponíveis para filtro:**
`a fazer` · `em andamento` · `em revisão` · `bloqueado` · `concluído`

> 💡 Dica: o nome pode ser parcial. `Bian` encontra `Bianca Garbin`.

---

## /meus-cards

```
/meus-cards
```
Mostra seus últimos 10 cards criados via Slack com título, responsável, prazo e link do Notion.

```
/meus-cards 20
```
Mostra os últimos 20.

---

## /editar-card

```
/editar-card
```
Lista seus cards recentes para escolher qual editar.

```
/editar-card Black Friday
```
Busca diretamente pelo título parcial.

Campos editáveis:
- `1` Prazo
- `2` Responsável
- `3` Revisor
- `4` Descrição

Após alterar um campo, você pode continuar editando outros ou digitar `cancelar` para sair.

---

## Dicas gerais

- A qualquer momento durante um fluxo, digite `cancelar` para sair
- Se demorar mais de **60 minutos** sem responder, a sessão expira automaticamente
- O agente aceita o **nome parcial** em qualquer campo de pessoa (ex: `Guil` → Guilherme Oliveira)
- Cards criados pelo Slack ficam salvos no histórico — use `/meus-cards` para consultar depois
