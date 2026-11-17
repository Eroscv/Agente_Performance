-- Registro de cada sincronização por cliente e fonte (Meta, Google, Shopify).
-- Gravado por db/sync_alerts.registrar_resultado() a cada execução, com sucesso ou erro.
-- É o que separa "zero vendas" (sincronização ok, sem linhas) de "carga falhou"
-- (sincronização com erro ou que não rodou). Lido por correlacao.v_status_cargas
-- e pela verificação de qualidade do gold.
CREATE TABLE IF NOT EXISTS `guara-db.ingestao.log_cargas_fontes` (
  executado_em TIMESTAMP NOT NULL,
  cliente      STRING    NOT NULL,
  fonte        STRING    NOT NULL,  -- meta | google | shopify
  sucesso      BOOL      NOT NULL,
  erro         STRING,              -- primeiros 500 caracteres; nunca token ou segredo
  origem       STRING               -- máquina que rodou a carga (hostname)
)
PARTITION BY DATE(executado_em)
CLUSTER BY cliente, fonte
OPTIONS (description = "Uma linha por sincronização de cliente e fonte, com sucesso ou erro");
