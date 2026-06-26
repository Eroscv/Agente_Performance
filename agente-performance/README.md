# ⚙️ Agente de Performance Guará

Agente de análise de performance de campanhas Meta Ads para os clientes da **Guará Marketing**.  
Processa dados de CSV → SQLite → publica relatórios automáticos no **Notion**.

---

## 📖 Plano de MVP

👉 **[Leia o MVP.md](./MVP.md)** — etapas, entregas, critérios de aceite e estrutura do projeto.

---

## 📐 Regras de Negócio

| Métrica | Regra |
|---|---|
| Fator de Imposto | `1,1215` (confirmado por Bianca — ignorar 1,1225) |
| ROAS | `Receita Shopify ÷ Investimento ajustado` — **nunca usar o do Meta** |
| CPA | `Investimento ajustado ÷ Nº de compras` — **nunca usar o do Meta** |
| CAC | `Investimento ajustado ÷ Novos clientes (Shopify via UTM)` |
| CPC | **Direto da plataforma Meta** — não recalcular |

---

## 🗃️ Fonte de Dados (MVP)

- **CSV Meta Ads** exportado manualmente → carregado no SQLite local
- Carga idempotente: `python -m db.loader`
- Sem dependência de APIs externas nesta fase

---

## ⚙️ Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # preencher NOTION_API_KEY, ANTHROPIC_API_KEY, SLACK_BOT_TOKEN
python -m db.loader    # carrega CSV → SQLite
```

---

## 📊 Databases Notion

| Cliente | ID |
|---|---|
| Source OF | `37a8a727-a8ff-81ba-aecd-e3ffff1f7f7e` |
| Consórcio Sorteado | `37a8a727-a8ff-814c-a1dc-e5e0b9f5993a` |
| Máximo Serviço | *(resolvido na primeira chamada)* |
| BE MUSHIN | *(resolvido na primeira chamada)* |
| Bazar do Precatório | *(resolvido na primeira chamada)* |

---

*Guará Marketing — uso interno*
