# 📋 Sistema de Transcrição de Reuniões

Automação para capturar, processar e armazenar reuniões a partir do Google Drive.

---

## Como funciona

```
Google Meet (Gemini)
       ↓
  Gera .docx com anotações automáticas
       ↓
  Você compartilha o arquivo no Google Drive
  com a conta de serviço
       ↓
  Cron roda a cada hora
       ↓
  Script detecta arquivo novo
       ↓
  Baixa, extrai e estrutura o conteúdo
       ↓
  Salva no banco SQLite (reunioes.db)
```

---

## Estrutura de arquivos

```
transcricao/
├── README.md                          ← este arquivo
├── sync_reunioes.py                   ← script principal de sync
├── run_sync.sh                        ← wrapper shell (usado pelo cron)
├── sync.log                           ← log de todas as execuções
├── credentials/
│   └── gdrive-service-account.json   ← credencial da conta de serviço Google
└── db/
    └── reunioes.db                    ← banco SQLite com todas as reuniões
```

---

## Pré-requisitos

- Python 3.10+
- Venv do agente-performance (já tem as libs necessárias)
- Credencial Google Drive em `credentials/gdrive-service-account.json`

**Libs usadas:**
- `google-auth` — autenticação com conta de serviço
- `google-api-python-client` — acesso à API do Drive
- `python-docx` — leitura de arquivos .docx

---

## Como adicionar uma nova reunião

1. Abra o Google Drive
2. Vá até a pasta compartilhada (ou qualquer pasta)
3. Faça upload (ou salve diretamente) o `.docx` gerado pelo Gemini
4. **Compartilhe o arquivo** com o e-mail da conta de serviço:
   ```
   transcricao@transcricao-510015.iam.gserviceaccount.com
   ```
   → Permissão: **Leitor** é suficiente
5. Aguarde até a próxima hora cheia — o sistema detecta e processa automaticamente

> **Dica:** Se quiser processar na hora, rode manualmente:
> ```bash
> /home/ubuntu/.openclaw/workspace/transcricao/run_sync.sh
> ```

---

## Cron automático

| Campo | Valor |
|---|---|
| Nome | `sync-reunioes-drive` |
| Frequência | Todo início de hora (`:00`) |
| Timezone | America/Sao_Paulo (BRT) |
| ID | `6212f2b6-dc74-4ede-ad4f-357280db9e27` |

---

## Banco de dados

**Arquivo:** `db/reunioes.db` (SQLite)

### Tabela `reunioes`

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | INTEGER | Chave primária auto-incremento |
| `data` | TEXT | Data da reunião (`YYYY-MM-DD`) |
| `hora` | TEXT | Hora da reunião (`HH:MM`) |
| `titulo` | TEXT | Nome da reunião |
| `participantes` | TEXT | Lista de participantes separada por vírgula |
| `resumo` | TEXT | Resumo executivo gerado pelo Gemini |
| `decisoes` | TEXT | Decisões tomadas na reunião |
| `proximas_etapas` | TEXT | Tarefas e responsáveis |
| `detalhes` | TEXT | Transcrição completa / detalhes da discussão |
| `fonte_arquivo` | TEXT | Nome do arquivo `.docx` original |
| `fonte_id` | TEXT | ID único do arquivo no Google Drive (evita duplicatas) |
| `criado_em` | TEXT | Timestamp UTC de quando foi salvo |

### Tabela `sync_log`

Registra cada execução do sync para auditoria.

| Coluna | Descrição |
|---|---|
| `fonte_id` | ID do arquivo no Drive |
| `nome` | Nome do arquivo |
| `status` | `ok` ou `erro` |
| `mensagem` | Detalhe do resultado ou mensagem de erro |
| `criado_em` | Timestamp da execução |

---

## Consultas úteis no banco

```sql
-- Ver todas as reuniões
SELECT id, data, hora, titulo, participantes
FROM reunioes
ORDER BY data DESC;

-- Buscar reuniões por participante
SELECT data, titulo, proximas_etapas
FROM reunioes
WHERE participantes LIKE '%Eros%'
ORDER BY data DESC;

-- Ver próximas etapas de uma data específica
SELECT titulo, proximas_etapas
FROM reunioes
WHERE data = '2026-09-28';

-- Ver histórico de syncs
SELECT nome, status, mensagem, criado_em
FROM sync_log
ORDER BY criado_em DESC
LIMIT 10;
```

---

## Execução manual

```bash
# Processar apenas arquivos novos (padrão)
/home/ubuntu/.openclaw/workspace/agente-performance/venv/bin/python3 \
  /home/ubuntu/.openclaw/workspace/transcricao/sync_reunioes.py

# Forçar reprocessamento de todos os arquivos
/home/ubuntu/.openclaw/workspace/agente-performance/venv/bin/python3 \
  /home/ubuntu/.openclaw/workspace/transcricao/sync_reunioes.py --todos
```

---

## Como o parser extrai os dados

O script lê o `.docx` gerado pelo Gemini e identifica automaticamente as seções:

| Seção no .docx | Campo no banco |
|---|---|
| Linha após `convidado` | `participantes` |
| Data/hora no nome do arquivo | `data` + `hora` |
| Bloco `Resumo` | `resumo` |
| Bloco `Decisões` | `decisoes` |
| Bloco `Próximas etapas` | `proximas_etapas` |
| Bloco `Detalhes` | `detalhes` |

> O formato esperado é o padrão do **Gemini Notes** gerado automaticamente pelo Google Meet.
> Arquivos com formato diferente podem precisar de ajuste no parser (`parsear_reuniao()` em `sync_reunioes.py`).

---

## Credencial Google Drive

| Campo | Valor |
|---|---|
| Projeto GCP | `transcricao-510015` |
| Conta de serviço | `transcricao@transcricao-510015.iam.gserviceaccount.com` |
| Escopo | `https://www.googleapis.com/auth/drive` |
| Arquivo local | `credentials/gdrive-service-account.json` |

> ⚠️ Nunca commite o arquivo `gdrive-service-account.json` em repositórios públicos.

---

## Troubleshooting

**Arquivo não está sendo detectado**
→ Verifique se o arquivo foi compartilhado com `transcricao@transcricao-510015.iam.gserviceaccount.com`

**Erro de autenticação**
→ Verifique se `credentials/gdrive-service-account.json` existe e está válido

**Dados extraídos incompletos**
→ O arquivo pode ter formato diferente do Gemini Notes padrão — ajuste a função `parsear_reuniao()` em `sync_reunioes.py`

**Ver logs da última execução**
```bash
tail -50 /home/ubuntu/.openclaw/workspace/transcricao/sync.log
```
