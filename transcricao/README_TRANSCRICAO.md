# 📄 Agente de Transcrição — Guia de Uso

Processa atas de reunião do Google Drive e cria cards no Notion automaticamente.
Também permite monitorar e reprocessar atas direto pelo Slack.

---

## Como funciona

```
Google Drive (.docx) → Agente → Notion (cards de tarefas)
```

1. O agente monitora uma pasta no Google Drive
2. Quando encontra uma ata `.docx` nova, baixa e processa com Claude
3. Extrai tarefas, responsáveis e prazos automaticamente
4. Cria cards no database **Agenda e Registros** do Notion

> O processamento roda automaticamente via cron. Você não precisa fazer nada — mas pode monitorar e intervir pelo Slack.

---

## Comandos disponíveis

| Comando | O que faz |
|---|---|
| `/status-atas [N]` | Ver as últimas N atas processadas com status |
| `/reprocessar-ata [nome]` | Forçar reprocessamento de uma ata |
| `/tutorial-transcricao` | Mostra este guia |

---

## /status-atas

```
/status-atas
```
Mostra as últimas 10 atas com status, mensagem de erro (se houver) e horário em BRT.

```
/status-atas 20
```
Mostra as últimas 20.

**O que aparece:**
```
📄 Status das Atas — últimas 10
Hoje: ✅ 3 processadas · ❌ 1 com erro
──────────────────────────────────────

✅ Source OF Reunião Semanal 2026-10-28.docx
   Salvo: Source OF: Reunião Semanal · 14:32 BRT

❌ POD Kombucha Briefing.docx
   Timeout na API · 09:15 BRT
```

**Ícones:**
- ✅ Processada com sucesso
- ❌ Falhou
- ⏭️ Pulada (já processada anteriormente)

---

## /reprocessar-ata

Use quando uma ata falhou e precisa ser reprocessada.

```
/reprocessar-ata POD Briefing
```
Busca pela ata com "POD Briefing" no nome e reprocessa.

```
/reprocessar-ata 2026-10-28
```
Busca por data no nome do arquivo.

> O reprocessamento acontece em **background**. Você recebe uma notificação quando terminar (sucesso ou falha).

**Se encontrar mais de uma ata com o mesmo trecho:**
```
Encontrei 2 atas com esse nome. Seja mais específico:
  • Source OF Reunião Semanal 2026-10-28.docx
  • Source OF Reunião Semanal 2026-10-27.docx
```

---

## Notificações automáticas de erro

Quando uma ata falha no processamento, o agente envia automaticamente um alerta no canal configurado:

```
⚠️ Falha ao processar ata
📄 POD Kombucha Briefing 2026-10-28.docx
❌ Timeout na chamada ao Claude

Para reprocessar: /reprocessar-ata POD Kombucha Briefing
```

---

## Formato das atas (.docx)

Para que o agente extraia corretamente as informações, a ata deve ter estas seções:

| Seção | Conteúdo esperado |
|---|---|
| **Data** | Data e hora da reunião |
| **Participantes** | Lista de quem participou |
| **Resumo** ou **Pauta** | O que foi discutido |
| **Próximas Etapas** ou **Tarefas** | Lista de ações com responsável e prazo |

> O agente é flexível — consegue extrair mesmo com variações de formatação. Mas quanto mais estruturada a ata, mais precisa será a extração.

---

## Cards gerados no Notion

Cada tarefa identificada na ata vira um card no Notion com:

- **Título:** no formato `CLIENTE: ÁREA - Tarefa`
  > Ex: `Source OF: ADS - Criar campanha de Black Friday`
- **Responsável:** identificado pelo nome na ata
- **Revisor:** atribuído automaticamente por área
- **Status:** A fazer
- **Prazo:** extraído da ata (ou +7 dias se não informado)
- **Cliente:** detectado pelo conteúdo
- **Área:** Performance, 3P ou Gestão (detectado pelo título)

---

## Validações automáticas

O agente valida cada card antes de criar no Notion:

| Situação | O que acontece |
|---|---|
| Título vazio ou genérico (`tarefa 1`, `to-do`) | ❌ Card não criado, erro logado |
| Título muito longo (>200 chars) | ❌ Card não criado |
| Área inconsistente com o título | ⚠️ Aviso no log (card criado mesmo assim) |
| Card já criado desta ata | ⏭️ Pulado (dedup automático) |

---

## Dicas

- Use `/status-atas` antes de perguntar "a ata foi processada?" — a resposta está lá
- Se uma ata falhou por erro de API (Claude/Gemini), tente `/reprocessar-ata` após alguns minutos
- O nome do arquivo `.docx` é usado para identificar a ata — use nomes descritivos
  > ✅ `Source OF Reunião Semanal 2026-10-28.docx`
  > ❌ `ata.docx` (difícil de identificar depois)
