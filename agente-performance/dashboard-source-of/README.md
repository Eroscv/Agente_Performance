# Relatoria Source Of

Dashboard estático (Vite + TypeScript, sem dependências de runtime) com o
desempenho da Source Of: Shopify, Meta Ads, Google Ads, canais, recuperação de
carrinho e criativos. Publicado via GitHub Pages.

## Rodar localmente

```bash
npm install
npm run dev
```

## Build de produção

```bash
npm install
npm run build
npm run preview   # opcional, para conferir o build final
```

O build gera `dist/`, pronto para ser servido como site estático.

## Deploy no GitHub Pages

O workflow `.github/workflows/deploy-dashboard-source-of.yml` (na raiz do
repositório) builda e publica `dashboard-source-of/dist` automaticamente a
cada push em `main` que toque esta pasta. Para habilitar:

1. No GitHub: **Settings → Pages → Source → GitHub Actions**.
2. Dê push nesta pasta (ou rode o workflow manualmente em *Actions*).
3. O site fica em `https://<usuário>.github.io/<repositório>/`.

## Atualizar os dados

Os dados ficam embutidos (sem backend) em `src/data.ts`, extraídos do
BigQuery (`guara-db`, dataset `guara_performance`/`correlacao`) para o
cliente `source_of`. Para atualizar:

1. Rode as consultas abaixo (projeto `guara-db`) e substitua os arrays
   correspondentes em `src/data.ts`:
   - `SHOPIFY` ← `guara_performance.shopify_diario` (cliente = source_of)
   - `ADS` ← `correlacao.fato_performance_diaria` (cliente_id = source_of)
   - `TOP_CAMPANHAS` ← `guara_performance.ads_diario`, agregado por
     `plataforma, campanha`, últimos 30 dias
   - `TOP_CRIATIVOS` ← `guara_performance.ads_diario`, agregado por
     `plataforma, campanha, adset_name, ad_name` (apenas `ad_id IS NOT NULL`),
     últimos 30 dias
   - `ABANDONED` ← `guara_performance.abandoned_checkout_daily`
     (cliente = source_of)
   - `LAST_SYNC` ← `MAX(criado_em)` de `shopify_diario`, `pedidos_diario` e
     `ads_diario`
2. `npm run build` para confirmar que compila sem erros de tipo.
3. Commit + push.

## Estrutura

- `src/data.ts` — dados brutos tipados (a única coisa que muda a cada
  atualização).
- `src/types.ts` — tipos compartilhados.
- `src/dates.ts` — cálculo dos períodos (última semana fechada, mês atual,
  etc.) e utilitários de data.
- `src/aggregate.ts` — soma/derivação de métricas por período e canal.
- `src/format.ts` — formatação (moeda, percentual, deltas).
- `src/charts.ts` — gráfico de linha SVG desenhado à mão (sem libs).
- `src/render.ts` — views de cada aba + estado da UI.
- `src/main.ts` — ponto de entrada.
