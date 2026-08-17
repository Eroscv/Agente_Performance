# 📱 Conversas WhatsApp — Chatbot Máximo — Julho 2026

Extração completa das conversas do número WhatsApp Business da **Máximo Serviço** via API [Whapi](https://whapi.cloud), cobrindo o período de **01/07/2026 a 31/07/2026**.

---

## 📊 Estatísticas do Período

| Métrica | Valor |
|---|---|
| **Conversas ativas** | 678 |
| **Total de mensagens** | 5.186 |
| **Período** | 01/07/2026 – 31/07/2026 |
| **Canal** | WhatsApp Business (Whapi) |
| **Extração** | 2026-07-31 |

---

## 📂 Arquivos

| Arquivo | Descrição | Tamanho |
|---|---|---|
| `conversas_julho2026.json` | Dados completos em JSON — todas as mensagens com metadados (timestamp, tipo, remetente, conteúdo, direção) | ~7,5 MB |
| `conversas_julho2026.txt` | Relatório legível — conversas em formato cronológico com direção `← LEAD` / `→ BOT` | ~600 KB |

---

## 🗂️ Estrutura do JSON

Cada entrada no array representa uma conversa:

```json
{
  "chat_id": "5511999999999@s.whatsapp.net",
  "chat_type": "contact",
  "name": "Nome do Contato",
  "last_ts": 1785497166,
  "msg_count": 23,
  "truncated": false,
  "messages": [
    {
      "id": "...",
      "timestamp": 1751400000,
      "from_me": false,
      "from": "5511999999999",
      "from_name": "Nome",
      "type": "text",
      "text": { "body": "Mensagem aqui" }
    }
  ]
}
```

### Campos principais

| Campo | Tipo | Descrição |
|---|---|---|
| `chat_id` | string | ID WhatsApp do chat |
| `chat_type` | string | `contact`, `group`, `broadcast` |
| `name` | string | Nome salvo na agenda |
| `msg_count` | int | Nº de mensagens no período |
| `truncated` | bool | `true` se limitado a 500 msgs |
| `messages[].from_me` | bool | `true` = bot enviou; `false` = lead enviou |
| `messages[].type` | string | `text`, `image`, `audio`, `video`, `document`, `button_reply`, `list_reply`, `interactive` |

---

## 🏷️ Tipos de Mensagem

| Tipo | Descrição |
|---|---|
| `text` | Mensagem de texto simples |
| `image` | Imagem (com ou sem legenda) |
| `audio` | Áudio / nota de voz |
| `video` | Vídeo (com ou sem legenda) |
| `document` | Arquivo/documento |
| `interactive` | Mensagem interativa enviada pelo bot (botões ou lista) |
| `button_reply` | Resposta do usuário clicando em botão |
| `list_reply` | Resposta do usuário selecionando item de lista |

---

## 🤖 Sobre o Chatbot Máximo

O **Bot Máximo** atende leads via WhatsApp para a empresa **Máximo Serviço** (serviços de portaria, limpeza, recepção, copa e vigilância). O fluxo principal:

```
Entrada
  ├── Quero contratar
  │     ├── Você é PJ? → Não → encerramento
  │     └── Sim → menu de serviços → cidade/bairro → CNPJ → contato preferido
  └── Quero um emprego → link trabalhe conosco
```

---

## ⚠️ Observações

- **1 chat truncado** (máx. 500 msgs): *João Victor - Comercial Máximo Serviço* — conversa operacional interna com volume alto.
- Chats com `msg_count: 0` são contatos com atividade recente mas sem mensagens acessíveis via API (geralmente o lead não enviou nada após o último contato anterior a 01/07).
- Previews de imagens (base64) foram omitidos do JSON para reduzir tamanho.

---

*Gerado automaticamente por Cesar — Agente de Performance Guará*
