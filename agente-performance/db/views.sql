-- =============================================================================
-- db/views.sql — Definição canônica das views federadas
--
-- ATENÇÃO: este arquivo é a fonte de verdade documental das views.
-- As views são TEMP VIEWs (limitação do SQLite com ATTACH cross-schema),
-- portanto não aparecem em .schema dos arquivos .db.
-- A implementação real está em db/connections.py (_V_PERFORMANCE_DDL etc.)
-- Mantenha este arquivo sincronizado com connections.py ao alterar views.
--
-- Para inspecionar ao vivo:
--   python3 -c "
--     from db.connections import get_federated_conn
--     c = get_federated_conn()
--     for r in c.execute(\"SELECT name FROM sqlite_temp_master WHERE type='view'\"):
--         print(r[0])
--   "
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- VIEW: v_performance
-- Fonte canônica de dados de performance — unifica Meta Ads + Google Ads
-- de todos os clientes com schema normalizado.
--
-- Colunas comuns:
--   cliente_key   TEXT   — 'source_of' | 'pod_kombucha'
--   cliente_nome  TEXT   — nome legível do cliente
--   plataforma    TEXT   — 'meta_ads' | 'google_ads'
--   data          TEXT   — YYYY-MM-DD
--   campanha      TEXT   — nome da campanha
--   campaign_id   TEXT   — id externo da campanha
--   tipo_campanha TEXT   — objetivo / tipo (Meta: objetivo; Google: tipo_campanha)
--   status        TEXT   — status do criativo/campanha
--   impressoes    REAL
--   cliques       REAL   — cliques_link (Meta) ou cliques (Google)
--   alcance       REAL   — só Meta Ads; NULL em Google
--   investimento  REAL
--   conversoes    REAL   — compras (Meta) ou conversoes (Google)
--   receita       REAL   — valor_compras (Meta) ou valor_conversao (Google)
--   roas          REAL   — receita / investimento (Meta: ajustado ×1.1215)
--   cpa           REAL   — investimento / conversoes
--   ctr           REAL   — percentual (0–100)
--   cpc_medio     REAL   — NULL em Meta (calculado na view campanhas_unificada)
--   video_3s      REAL   — só Meta Ads; NULL em Google
--   thruplays     REAL   — só Meta Ads; NULL em Google
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TEMP VIEW IF NOT EXISTS v_performance AS

-- Source OF — Google Ads
SELECT
    'source_of'   AS cliente_key,
    'Source OF'   AS cliente_nome,
    'google_ads'  AS plataforma,
    data, campanha, campaign_id, tipo_campanha, status,
    COALESCE(impressoes,       0) AS impressoes,
    COALESCE(cliques,          0) AS cliques,
    NULL                          AS alcance,
    COALESCE(investimento,     0) AS investimento,
    COALESCE(conversoes,       0) AS conversoes,
    COALESCE(valor_conversao,  0) AS receita,
    CASE WHEN investimento > 0
         THEN ROUND(valor_conversao / investimento, 4) END AS roas,
    CASE WHEN conversoes > 0
         THEN ROUND(investimento / conversoes, 2)      END AS cpa,
    ctr,
    cpc_medio,
    NULL AS video_3s,
    NULL AS thruplays
FROM sof_gads.source_of_gads_diario

UNION ALL

-- Source OF — Meta Ads
-- ROAS ajustado: divide por investimento × 1.1215 (markup operacional)
SELECT
    'source_of', 'Source OF', 'meta_ads',
    data, campanha, campaign_id, objetivo, status_criativo,
    COALESCE(impressoes,    0),
    COALESCE(cliques_link,  0),
    COALESCE(alcance,       0),
    COALESCE(investimento,  0),
    COALESCE(compras,       0),
    COALESCE(valor_compras, 0),
    CASE WHEN investimento * 1.1215 > 0
         THEN ROUND(valor_compras / (investimento * 1.1215), 4) END,
    CASE WHEN compras > 0
         THEN ROUND(investimento * 1.1215 / compras, 2)        END,
    CASE WHEN impressoes > 0
         THEN ROUND(cliques_link / impressoes * 100.0, 4)      END,
    NULL,
    COALESCE(video_3s,    0),
    COALESCE(thruplays,   0)
FROM sof_meta.source_of_meta_ads_diario

UNION ALL

-- POD Kombucha — Google Ads
SELECT
    'pod_kombucha', 'POD Kombucha', 'google_ads',
    data, campanha, campaign_id, tipo_campanha, NULL,
    COALESCE(impressoes,      0),
    COALESCE(cliques,         0),
    NULL,
    COALESCE(investimento,    0),
    COALESCE(conversoes,      0),
    COALESCE(valor_conversao, 0),
    CASE WHEN investimento > 0
         THEN ROUND(valor_conversao / investimento, 4) END,
    CASE WHEN conversoes > 0
         THEN ROUND(investimento / conversoes, 2)      END,
    ctr, cpc_medio, NULL, NULL
FROM pod_gads.pod_kombucha_gads_diario

UNION ALL

-- POD Kombucha — Meta Ads
SELECT
    'pod_kombucha', 'POD Kombucha', 'meta_ads',
    data, campanha, campaign_id, objetivo, status_criativo,
    COALESCE(impressoes,    0),
    COALESCE(cliques_link,  0),
    COALESCE(alcance,       0),
    COALESCE(investimento,  0),
    COALESCE(compras,       0),
    COALESCE(valor_compras, 0),
    CASE WHEN investimento * 1.1215 > 0
         THEN ROUND(valor_compras / (investimento * 1.1215), 4) END,
    CASE WHEN compras > 0
         THEN ROUND(investimento * 1.1215 / compras, 2)        END,
    CASE WHEN impressoes > 0
         THEN ROUND(cliques_link / impressoes * 100.0, 4)      END,
    NULL,
    COALESCE(video_3s,   0),
    COALESCE(thruplays,  0)
FROM pod_meta.pod_kombucha_meta_ads_diario;


-- ─────────────────────────────────────────────────────────────────────────────
-- VIEW: campanhas_unificada
-- Interface pública usada por agent/queries/* — renomeia colunas de v_performance
-- para o schema legado esperado pelas queries (data_inicio/data_fim, valor_compras etc.)
--
-- Colunas adicionais sempre NULL (legado / não rastreadas):
--   frequencia, cpm, compras_app, compras_site, cliques_todos,
--   cpc_link, visualizacoes_landing, reproducoes_video,
--   conversas_iniciadas, adicoes_carrinho, checkout_iniciado
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TEMP VIEW IF NOT EXISTS campanhas_unificada AS
SELECT
    data          AS data_inicio,
    data          AS data_fim,
    campanha,
    cliente_key,
    cliente_nome,
    plataforma,
    investimento,
    receita       AS valor_compras,
    conversoes    AS compras,
    impressoes,
    cliques       AS cliques_link,
    video_3s,
    thruplays,
    roas          AS roas_compras,
    cpa           AS custo_por_compra,
    ctr           AS ctr_link,
    alcance,
    NULL AS frequencia,
    NULL AS cpm,
    NULL AS compras_app,
    NULL AS compras_site,
    NULL AS cliques_todos,
    NULL AS cpc_link,
    NULL AS visualizacoes_landing,
    NULL AS reproducoes_video,
    NULL AS conversas_iniciadas,
    NULL AS adicoes_carrinho,
    NULL AS checkout_iniciado
FROM v_performance;


-- ─────────────────────────────────────────────────────────────────────────────
-- VIEW: shopify_receita_diaria
-- Proxy para shopify_src.shopify_receita_diaria (ATTACH alias).
-- Expõe a tabela Shopify no namespace principal da conexão federada.
--
-- Schema original (shopify_receita.db):
--   data DATE UNIQUE, receita REAL, pedidos INT, novos_clientes INT,
--   recompradores INT, ticket_medio REAL, total_desconto REAL,
--   pedidos_acima_300 INT, atualizado_em TEXT
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TEMP VIEW IF NOT EXISTS shopify_receita_diaria AS
SELECT * FROM shopify_src.shopify_receita_diaria;


-- =============================================================================
-- NOTAS DE MANUTENÇÃO
--
-- Adicionar novo cliente:
--   1. Adicionar entrada em PERFORMANCE_DBS (db/connections.py)
--   2. Adicionar ATTACH em _build_federated_conn() com novo alias
--   3. Adicionar UNION ALL em v_performance (aqui e em _V_PERFORMANCE_DDL)
--   4. Rodar: pytest tests/test_infraestrutura.py
--
-- Adicionar nova coluna a uma fonte:
--   1. Alterar o SELECT do UNION correspondente em v_performance
--   2. Se expor em campanhas_unificada, adicionar coluna no SELECT dessa view
--   3. Manter os outros UNIONs com NULL para a nova coluna
--
-- Inspecionar views ao vivo:
--   python3 -c "
--     from db.connections import get_federated_conn
--     c = get_federated_conn()
--     print(c.execute('SELECT name FROM sqlite_temp_master WHERE type=\"view\"').fetchall())
--   "
-- =============================================================================
