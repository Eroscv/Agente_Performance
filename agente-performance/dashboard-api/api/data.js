const { BigQuery } = require("@google-cloud/bigquery");

const PROJECT_ID = "guara-db";
const CLIENTE = "source_of";
const ALLOWED_ORIGINS = new Set([
  "https://eroscv.github.io",
  "http://localhost:4173",
  "http://localhost:5173",
]);

let bqClient = null;
function getClient() {
  if (bqClient) return bqClient;
  const raw = process.env.GCP_SA_KEY;
  if (!raw) throw new Error("GCP_SA_KEY env var not set");
  const credentials = JSON.parse(raw);
  bqClient = new BigQuery({ projectId: PROJECT_ID, credentials });
  return bqClient;
}

function setCors(req, res) {
  const origin = req.headers.origin;
  if (origin && ALLOWED_ORIGINS.has(origin)) {
    res.setHeader("Access-Control-Allow-Origin", origin);
  }
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Vary", "Origin");
}

const QUERIES = {
  shopify: `
    SELECT data, receita, pedidos, ticket_medio, novos_clientes, recompradores,
           total_desconto, frete, pedidos_gt300, gross_sales,
           sessoes, sessoes_compra, taxa_conversao
    FROM \`${PROJECT_ID}.ingestao.ing_shopify_resumo_diario\`
    WHERE cliente = @cliente
    ORDER BY data`,
  ads: `
    SELECT data, canal, investimento, pedidos, receita, novos_clientes, roas_real AS roas
    FROM \`${PROJECT_ID}.correlacao.fato_performance_diaria\`
    WHERE cliente_id = @cliente
    ORDER BY data`,
  campanhas: `
    SELECT data, plataforma, campanha,
           SUM(investimento) AS inv, SUM(compras) AS compras, SUM(receita) AS receita
    FROM \`${PROJECT_ID}.ingestao.ing_ads_diario\`
    WHERE cliente = @cliente AND nivel = 'campanha'
    GROUP BY data, plataforma, campanha
    ORDER BY data`,
  criativos: `
    SELECT data, campanha, adset, ad_id AS ad, ad_name AS ad_nome,
           investimento AS inv, compras, receita, cliques_link AS cliques
    FROM \`${PROJECT_ID}.ingestao.ing_ads_diario\`
    WHERE cliente = @cliente AND nivel = 'anuncio' AND plataforma = 'meta'
    ORDER BY data`,
  abandoned: `
    SELECT data,
           SUM(checkouts) AS checkouts,
           SUM(subtotal_total) AS subtotal,
           SUM(recuperados) AS recuperados,
           SAFE_DIVIDE(SUM(recuperados), SUM(checkouts)) AS taxa_recuperacao
    FROM \`${PROJECT_ID}.ingestao.ing_checkout_abandonado_diario\`
    WHERE cliente = @cliente
    GROUP BY data
    ORDER BY data`,
};

async function runQuery(bq, key) {
  const [rows] = await bq.query({
    query: QUERIES[key],
    params: { cliente: CLIENTE },
    location: "US",
  });
  return rows;
}

module.exports = async (req, res) => {
  setCors(req, res);
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }

  try {
    const bq = getClient();
    const [shopify, ads, campanhas, criativos, abandoned, lastSyncRows] = await Promise.all([
      runQuery(bq, "shopify"),
      runQuery(bq, "ads"),
      runQuery(bq, "campanhas"),
      runQuery(bq, "criativos"),
      runQuery(bq, "abandoned"),
      bq.query({
        query: `SELECT table_id, TIMESTAMP_MILLIS(last_modified_time) AS last_modified
                 FROM \`${PROJECT_ID}.guara_performance.__TABLES__\`
                 WHERE table_id IN ('pedidos_diario', 'ads_diario', 'shopify_diario')`,
      }).then(([r]) => r),
    ]);

    const lastSync = {};
    for (const row of lastSyncRows) {
      lastSync[row.table_id] = Math.floor(new Date(row.last_modified.value).getTime() / 1000);
    }

    res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=600");
    res.status(200).json({
      generatedAt: new Date().toISOString(),
      shopify,
      ads,
      campanhas,
      criativos,
      abandoned,
      lastSync,
    });
  } catch (err) {
    res.status(500).json({ error: String(err && err.message ? err.message : err) });
  }
};
