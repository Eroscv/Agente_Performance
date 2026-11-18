-- Dataset de teste, separado dos de produção (guara_performance, ingestao, correlacao).
-- Toda tabela criada aqui some sozinha em 7 dias. Regra: teste, rascunho e cópia temporária
-- só aqui; nunca nos datasets de produção (projeto de dados, seção 5).
CREATE SCHEMA IF NOT EXISTS `guara-db.dev`
OPTIONS (
  location = 'US',
  default_table_expiration_days = 7,
  description = 'Testes e rascunhos. Tabelas expiram em 7 dias. Nada de produção lê daqui.'
);
