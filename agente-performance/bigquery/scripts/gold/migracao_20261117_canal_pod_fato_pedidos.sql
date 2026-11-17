-- Migração única (17/11/2026): canal da POD em correlacao.fato_pedidos pela regra do Colab.
--
-- fato_pedidos é insert-only (o MERGE nunca atualiza linhas), então a mudança de regra em
-- proc_load_gold.sql só vale para pedidos novos. Esta migração corrige os pedidos da POD que
-- já estavam lá: 483 pedidos (R$ 146 mil) de klaviyo, manychat, grupo de WhatsApp, crm e
-- chatgpt.com que estavam em 'outros' e pela regra do Colab são 'direto' (Orgânico/Direto).
-- Só a coluna canal muda, e só para a POD. Source Of e Botica ficam como estão.
--
-- Backup antes: correlacao.fato_pedidos_bkp_20261117 (snapshot, expira em 30 dias).
-- Voltar atrás:
--   UPDATE `guara-db.correlacao.fato_pedidos` T SET canal = B.canal
--   FROM `guara-db.correlacao.fato_pedidos_bkp_20261117` B
--   WHERE T.order_id = B.order_id AND T.cliente_id = B.cliente_id AND T.cliente_id = 'pod_kombucha';

CREATE SNAPSHOT TABLE IF NOT EXISTS `guara-db.correlacao.fato_pedidos_bkp_20261117`
CLONE `guara-db.correlacao.fato_pedidos`
OPTIONS (expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 30 DAY));

UPDATE `guara-db.correlacao.fato_pedidos`
SET canal = `guara-db.correlacao.fn_canal_pod`(utm_source)
WHERE cliente_id = 'pod_kombucha'
  AND canal != `guara-db.correlacao.fn_canal_pod`(utm_source);
