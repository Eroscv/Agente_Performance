# Texto para testadores — Agente de Cards

Olá! 👋

Você foi selecionado para testar o **Agente de Cards** — ele cria e acompanha tarefas no Notion direto pelo Slack, sem precisar abrir o Notion.

---

**📖 Leia antes de começar:**
https://github.com/Eroscv/Agente_Performance/blob/main/transcricao/README.md

---

**Por onde começar:**

```
/criar-card
```

O agente vai fazer 8 perguntas em sequência: cliente, frente, entrega, responsável, revisor, prazo, descrição e confirmação. Responda cada uma normalmente — pode digitar o número da lista ou parte do nome.

**O que mais testar:**
- `/status-cards` — ver cards em aberto por responsável
- `/status-cards Bianca em andamento` — filtro combinado de nome + status
- `/meus-cards` — histórico dos seus cards criados
- `/editar-card` — alterar prazo ou responsável de um card sem recriar
- Prazo em formatos diferentes: `amanhã`, `hoje`, `+7`, `15/11`, `2026-11-15`
- Tentar colocar o mesmo usuário como responsável e revisor (deve ser bloqueado)
- Cancelar no meio do fluxo com `cancelar`

---

**Período de testes: 1 semana.**

Me manda o feedback aqui no canal ou em DM:

1. O que funcionou e foi útil no dia a dia
2. O que travou ou não avançou como esperado
3. O que sentiu falta (campo, comando, comportamento)
4. Qualquer bug — o que você digitou, o que esperava e o que aconteceu

Obrigado! 🙏
