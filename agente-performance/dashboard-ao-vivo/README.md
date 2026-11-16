# Painel Guará ao vivo

Painel de mídia e loja de Source Of, POD Kombucha e Botica, lido direto do BigQuery
(`guara-db`) e atualizado a cada 30 segundos enquanto a aba está aberta.

- `index.html`: o painel (HTML e JS puros, Chart.js por CDN).
- `api/dados.js`: função serverless da Vercel que faz só consultas de leitura no BigQuery.
- `servidor-local.js`: roda os dois no seu computador, sem Vercel.

## O que o painel mostra

| Bloco | Fonte |
|---|---|
| Indicadores, gráfico diário e tabela por canal | `correlacao.fato_performance_diaria` |
| Campanhas (Meta e Google) e anúncios do Meta | `ingestao.ing_ads_diario` (`entra_no_total`) |
| Loja | `ingestao.ing_shopify_resumo_diario` |
| Aviso de dado atrasado e rodapé | maior data por fonte e hora da última carga do gold |

- **Investimento** é o `investimento_ajustado` do gold: Meta × 1,1383, Google × 1.
- **ROAS** = receita ÷ investimento. **CAC** = investimento ÷ novos clientes. **Ticket** = receita ÷ pedidos.
- Cada indicador compara com o período anterior de mesmo tamanho. "Este mês" compara com os mesmos dias do mês passado.
- As tabelas de campanhas e anúncios mostram o investimento **bruto** e a receita que a
  plataforma atribui (Meta/Google), não a receita da loja.

Filtros: cliente, período (hoje, ontem, 7/14/30 dias, este mês, mês passado ou datas livres),
canais e busca nas tabelas. Clicar no cabeçalho de uma coluna ordena. Os filtros ficam na URL,
então o link copiado abre o mesmo recorte.

Atualização: a cada 30 s o painel consulta a API. Em segundo plano a aba pausa, e ao voltar busca
na hora. O botão **Pausar** para a atualização. A API responde com cache de 20 s
(`s-maxage=20`), então várias abas abertas não repetem as consultas no BigQuery.

## Rodar no seu computador

```bash
cd agente-performance/dashboard-ao-vivo
npm install
GOOGLE_APPLICATION_CREDENTIALS=/caminho/da/service-account.json npm run local
```

Abre em http://localhost:4173.

## Publicar na Vercel

1. Criar um projeto na Vercel a partir do repositório, com **Root Directory**
   `agente-performance/dashboard-ao-vivo` e **Framework Preset** `Other` (sem build).
2. Em Settings → Environment Variables, criar `GCP_SA_KEY` (tipo **Sensitive**) com o JSON de
   uma service account **só de leitura**: BigQuery Data Viewer nos datasets `correlacao` e
   `ingestao`, e BigQuery Job User no projeto. Nunca coloque essa chave em chat, commit ou arquivo.
3. Deploy. O painel fica em `https://<projeto>.vercel.app`.

Acesso: com a proteção de deploy padrão da Vercel (Vercel Authentication), só quem é do time
na Vercel abre o painel. Para liberar para outras pessoas sem conta, use um domínio próprio
com senha (Password Protection) ou adicione os membros ao time.
