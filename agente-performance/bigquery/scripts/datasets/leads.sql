-- Central de leads, primeira versão (projeto de dados, seção 7): formulário da LP, formulário
-- de lead do Meta e WhatsApp. LinkedIn e ManyChat ficam para depois.
-- Bruto: a resposta de cada fonte como veio. Tratado: um lead por linha, campos comuns.
-- O "pronto para uso" (correlacao.fato_leads, um lead por pessoa e cliente) entra com a carga.
-- E-mail e telefone são dados pessoais: este dataset não deve ser lido por app de relatório.
CREATE SCHEMA IF NOT EXISTS `guara-db.leads`
OPTIONS (
  location = 'US',
  description = 'Central de leads: dados pessoais (e-mail, telefone). Acesso só da carga da Central e do responsável.'
);

CREATE TABLE IF NOT EXISTS `guara-db.leads.raw_lp_formulario` (
  recebido_em  TIMESTAMP NOT NULL,
  cliente      STRING    NOT NULL,
  envio_id     STRING    NOT NULL,   -- id do envio no formulário/site
  payload_json JSON      NOT NULL
)
PARTITION BY DATE(recebido_em)
CLUSTER BY cliente
OPTIONS (description = 'Formulário da landing page, como veio');

CREATE TABLE IF NOT EXISTS `guara-db.leads.raw_meta_lead_ads` (
  recebido_em  TIMESTAMP NOT NULL,
  cliente      STRING    NOT NULL,
  leadgen_id   STRING    NOT NULL,   -- id do lead na Graph API
  form_id      STRING,
  payload_json JSON      NOT NULL
)
PARTITION BY DATE(recebido_em)
CLUSTER BY cliente
OPTIONS (description = 'Formulário de lead do Meta, como veio da Graph API');

CREATE TABLE IF NOT EXISTS `guara-db.leads.raw_whatsapp` (
  recebido_em         TIMESTAMP NOT NULL,
  cliente             STRING    NOT NULL,
  telefone            STRING    NOT NULL,
  nome_contato        STRING,
  primeira_mensagem_em TIMESTAMP,
  campanha_origem     STRING,
  payload_json        JSON      -- só metadados do contato; o texto das conversas não entra
)
PARTITION BY DATE(recebido_em)
CLUSTER BY cliente
OPTIONS (description = 'WhatsApp: só os campos essenciais do contato, sem o texto das conversas');

CREATE TABLE IF NOT EXISTS `guara-db.leads.ing_leads` (
  lead_id        STRING    NOT NULL,  -- fonte + id na fonte
  cliente        STRING    NOT NULL,
  fonte          STRING    NOT NULL,  -- lp | meta | whatsapp
  recebido_em    TIMESTAMP NOT NULL,
  nome           STRING,
  email          STRING,
  telefone       STRING,
  utm_source     STRING,
  utm_campaign   STRING,
  campanha_id    STRING,
  formulario_id  STRING,
  mensagem       STRING,              -- campo livre do formulário; nunca texto de conversa do WhatsApp
  atualizado_em  TIMESTAMP NOT NULL
)
PARTITION BY DATE(recebido_em)
CLUSTER BY cliente, fonte
OPTIONS (description = 'Um lead por linha, campos comuns das três fontes');
