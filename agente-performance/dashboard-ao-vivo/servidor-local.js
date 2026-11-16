// Roda o painel no seu computador, com a sua credencial do BigQuery:
//   GOOGLE_APPLICATION_CREDENTIALS=/caminho/sa.json npm run local   → http://localhost:4173
const http = require("http");
const fs = require("fs");
const path = require("path");
const dados = require("./api/dados.js");

const PORTA = Number(process.env.PORT || 4173);
http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://localhost");
  if (u.pathname === "/api/dados") {
    const shim = {
      status(c) { res.statusCode = c; return shim; },
      setHeader: (k, v) => res.setHeader(k, v),
      json(o) { res.setHeader("Content-Type", "application/json; charset=utf-8"); res.end(JSON.stringify(o)); },
    };
    req.query = Object.fromEntries(u.searchParams);
    return dados(req, shim);
  }
  if (u.pathname === "/" || u.pathname === "/index.html") {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    return fs.createReadStream(path.join(__dirname, "index.html")).pipe(res);
  }
  res.statusCode = 404; res.end("não encontrado");
}).listen(PORTA, () => console.log(`Painel em http://localhost:${PORTA}`));
