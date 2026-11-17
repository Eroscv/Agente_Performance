-- Consumo do Agente de Performance: mesma forma da view local campanhas_unificada
-- (db/duck.py), sobre correlacao.v_agente_performance. As colunas que a view local devolve
-- sempre nulas continuam nulas aqui, para o agente responder igual nos dois caminhos.
CREATE OR REPLACE VIEW `guara-db.correlacao.v_agente_campanhas` AS
SELECT
  data AS data_inicio,
  data AS data_fim,
  campanha,
  cliente_key,
  cliente_nome,
  plataforma,
  investimento,
  receita AS valor_compras,
  conversoes AS compras,
  impressoes,
  cliques AS cliques_link,
  video_3s,
  thruplays,
  roas AS roas_compras,
  cpa AS custo_por_compra,
  ctr AS ctr_link,
  alcance,
  CAST(NULL AS FLOAT64) AS frequencia,
  CAST(NULL AS FLOAT64) AS cpm,
  CAST(NULL AS FLOAT64) AS compras_app,
  CAST(NULL AS FLOAT64) AS compras_site,
  CAST(NULL AS FLOAT64) AS cliques_todos,
  CAST(NULL AS FLOAT64) AS cpc_link,
  CAST(NULL AS FLOAT64) AS visualizacoes_landing,
  CAST(NULL AS FLOAT64) AS reproducoes_video,
  CAST(NULL AS FLOAT64) AS conversas_iniciadas,
  CAST(NULL AS FLOAT64) AS adicoes_carrinho,
  CAST(NULL AS FLOAT64) AS checkout_iniciado
FROM `guara-db.correlacao.v_agente_performance`;
