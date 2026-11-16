// GET /api/dados?cliente=source_of&inicio=2026-11-01&fim=2026-11-16
//
// Lê o BigQuery (guara-db) só com consultas de leitura e devolve o que o painel desenha.
// Credencial: GCP_SA_KEY (JSON da service account de leitura, env var secreta na Vercel).
// Sem ela, usa a credencial padrão do Google (GOOGLE_APPLICATION_CREDENTIALS), para rodar local.
const { BigQuery } = require("@google-cloud/bigquery");

const PROJECT_ID = "guara-db";
const CLIENTES = new Set(["source_of", "pod_kombucha", "botica"]);
const DATA_RE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_DIAS = 400;

let bq = null;
function cliente() {
  if (bq) return bq;
  const raw = process.env.GCP_SA_KEY;
  bq = raw
    ? new BigQuery({ projectId: PROJECT_ID, credentials: JSON.parse(raw) })
    : new BigQuery({ projectId: PROJECT_ID });
  return bq;
}

const T = (t) => `\`${PROJECT_ID}.${t}\``;

const QUERIES = {
  // Dia x canal, do gold. investimento_ajustado = Meta x fator de imposto, Google x1.
  diario: `
    SELECT CAST(data AS STRING) AS data, canal,
           CAST(investimento AS FLOAT64) AS investimento,
           CAST(investimento_ajustado AS FLOAT64) AS investimento_ajustado,
           pedidos, receita, novos_clientes, cliques_link, impressoes
    FROM ${T("correlacao.fato_performance_diaria")}
    WHERE cliente_id = @cliente AND data BETWEEN @inicio AND @fim
    ORDER BY data, canal`,
  // Meta só tem linhas por anúncio e Google por campanha; entra_no_total é a mesma regra do gold.
  campanhas: `
    SELECT plataforma, campanha,
           CAST(SUM(investimento) AS FLOAT64) AS investimento,
           SUM(compras) AS compras, CAST(SUM(receita) AS FLOAT64) AS receita,
           SUM(cliques_link) AS cliques, SUM(impressoes) AS impressoes
    FROM ${T("ingestao.ing_ads_diario")} AS a
    WHERE cliente = @cliente AND entra_no_total AND data BETWEEN @inicio AND @fim
    GROUP BY 1, 2
    HAVING SUM(a.investimento) > 0
    ORDER BY investimento DESC`,
  criativos: `
    SELECT ANY_VALUE(ad_name) AS anuncio, ANY_VALUE(campanha) AS campanha,
           ANY_VALUE(adset_name) AS conjunto, ad_id,
           CAST(SUM(investimento) AS FLOAT64) AS investimento,
           SUM(compras) AS compras, CAST(SUM(receita) AS FLOAT64) AS receita,
           SUM(cliques_link) AS cliques, SUM(impressoes) AS impressoes
    FROM ${T("ingestao.ing_ads_diario")} AS a
    WHERE cliente = @cliente AND nivel = 'anuncio' AND plataforma = 'meta'
      AND data BETWEEN @inicio AND @fim
    GROUP BY ad_id
    HAVING SUM(a.investimento) > 0
    ORDER BY investimento DESC
    LIMIT 60`,
  loja: `
    SELECT CAST(data AS STRING) AS data, CAST(receita AS FLOAT64) AS receita, pedidos,
           novos_clientes, recompradores, CAST(ticket_medio AS FLOAT64) AS ticket_medio,
           sessoes, sessoes_compra, taxa_conversao
    FROM ${T("ingestao.ing_shopify_resumo_diario")}
    WHERE cliente = @cliente AND data BETWEEN @inicio AND @fim
    ORDER BY data`,
  frescor: `
    SELECT 'midia_' || plataforma AS fonte, CAST(MAX(data) AS STRING) AS ate
    FROM ${T("ingestao.ing_ads_diario")} WHERE cliente = @cliente GROUP BY plataforma
    UNION ALL
    SELECT 'loja', CAST(MAX(data) AS STRING)
    FROM ${T("ingestao.ing_shopify_resumo_diario")} WHERE cliente = @cliente`,
  carga: `
    SELECT CAST(TIMESTAMP_MILLIS(last_modified_time) AS STRING) AS em
    FROM ${T("correlacao.__TABLES__")} WHERE table_id = 'fato_performance_diaria'`,
};

function erro(res, status, msg) {
  res.status(status).json({ erro: msg });
}

module.exports = async (req, res) => {
  const q = req.query || {};
  const cli = String(q.cliente || "");
  const inicio = String(q.inicio || "");
  const fim = String(q.fim || "");
  if (!CLIENTES.has(cli)) return erro(res, 400, "cliente inválido");
  if (!DATA_RE.test(inicio) || !DATA_RE.test(fim)) return erro(res, 400, "datas no formato AAAA-MM-DD");
  const dias = (Date.parse(fim) - Date.parse(inicio)) / 86400000;
  if (!(dias >= 0 && dias <= MAX_DIAS)) return erro(res, 400, `período entre 1 e ${MAX_DIAS} dias`);

  try {
    const params = { cliente: cli, inicio, fim };
    const nomes = Object.keys(QUERIES);
    const respostas = await Promise.all(
      nomes.map((k) =>
        cliente()
          .query({ query: QUERIES[k], params, location: "US" })
          .then(([rows]) => rows)
      )
    );
    const out = Object.fromEntries(nomes.map((k, i) => [k, respostas[i]]));
    out.carga = out.carga[0] ? out.carga[0].em : null;
    out.geradoEm = new Date().toISOString();
    // O painel pede a cada 30s; o cache da Vercel segura 20s para várias abas não repetirem a consulta.
    res.setHeader("Cache-Control", "s-maxage=20, stale-while-revalidate=10");
    res.status(200).json(out);
  } catch (e) {
    console.error(e && e.message ? e.message : e);
    erro(res, 500, "falha ao consultar o BigQuery");
  }
};
