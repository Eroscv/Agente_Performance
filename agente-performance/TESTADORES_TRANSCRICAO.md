# Texto para testadores — Agente de Transcrição

Olá! 👋

Você foi selecionado para testar o **Agente de Transcrição** — ele pega atas de reunião do Google Drive, extrai as tarefas automaticamente e cria cards no Notion sem intervenção manual.

---

**📖 Leia antes de começar:**
https://github.com/Eroscv/Agente_Performance/blob/main/transcricao/README_TRANSCRICAO.md

---

**Como funciona na prática:**

O agente roda automaticamente — você não precisa fazer nada para ele processar uma ata nova. O que precisa testar é o **monitoramento e a intervenção** via Slack:

```
/status-atas
```
Mostra as últimas atas processadas com status (✅ sucesso · ❌ erro · ⏭️ pulada).

```
/reprocessar-ata <trecho do nome>
```
Força reprocessamento de uma ata que falhou.

---

**O que mais testar:**
- `/status-atas 20` — aumentar o limite para ver mais histórico
- `/reprocessar-ata` com uma ata que falhou — ver se o feedback chega de volta
- Subir uma ata nova no Drive com nome descritivo e aguardar o processamento automático
- Verificar se os cards criados no Notion estão com responsável, prazo e área corretos
- Testar com ata mal formatada — ver como o agente lida

**O que observar nos cards gerados:**
- Título no formato certo: `CLIENTE: ÁREA - Tarefa`
- Responsável identificado corretamente pelo nome na ata
- Prazo extraído da ata (ou +7 dias se não informado)
- Status chegando como "A fazer" no Notion

---

**Período de testes: 1 semana.**

Me manda o feedback aqui no canal ou em DM:

1. Cards criados corretamente a partir das atas
2. Algo que o agente não soube extrair ou extraiu errado (responsável, prazo, área)
3. Erros que apareceram no `/status-atas`
4. Qualquer comportamento inesperado

Quanto mais detalhe melhor: nome da ata, o que esperava e o que aconteceu.

Obrigado! 🙏
