# dashboard-source-of-api

Proxy serverless (Vercel) que consulta o BigQuery (`guara-db`) com uma
service account de leitura e devolve os dados em JSON para o dashboard
estático em `dashboard-source-of/` (GitHub Pages).

O dashboard GitHub Pages não pode consultar o BigQuery diretamente — isso
exigiria expor credenciais no código público do navegador. Esta função roda
no servidor da Vercel, guarda a credencial como variável de ambiente secreta
e expõe só um endpoint JSON com CORS restrito ao domínio do dashboard.

## Endpoint

`GET /api/data` → `{ shopify, ads, campanhas, criativos, abandoned, lastSync, generatedAt }`

Resposta cacheada por 5 minutos (`s-maxage=300`).

## Configuração necessária (feita manualmente no painel da Vercel, não por mim)

1. Criar uma service account no GCP com papel **BigQuery Data Viewer** +
   **BigQuery Job User**, restrita aos datasets `guara_performance`,
   `ingestao` e `correlacao` do projeto `guara-db`.
2. Gerar uma chave JSON para essa service account.
3. No painel da Vercel → Project Settings → Environment Variables, criar
   `GCP_SA_KEY` (tipo **Sensitive**) com o conteúdo inteiro do JSON da chave,
   nos ambientes Production e Preview.
4. Fazer um novo deploy (ou redeploy) para a env var entrar em efeito.

Nunca cole essa chave em um chat, commit ou arquivo do repositório — ela só
deve existir como env var secreta na Vercel.
