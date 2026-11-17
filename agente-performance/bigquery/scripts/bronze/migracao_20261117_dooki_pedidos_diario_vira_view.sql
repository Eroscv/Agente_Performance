-- Migração única (17/11/2026): a tabela guara_performance.dooki_pedidos_diario sai para dar
-- lugar a uma view de mesmo nome (bigquery/scripts/views/guara_performance/dooki_pedidos_diario.sql).
-- Ninguém grava na tabela desde 05/11 e nenhuma view a lê, mas ela ainda é consultada à mão.
-- Rodar ANTES do arquivo da view. Backup: snapshot por 90 dias.
-- Voltar atrás: DROP VIEW da nova e
--   CREATE TABLE `guara-db.guara_performance.dooki_pedidos_diario`
--   CLONE `guara-db.guara_performance.dooki_pedidos_diario_bkp_20261117`;
CREATE SNAPSHOT TABLE IF NOT EXISTS `guara-db.guara_performance.dooki_pedidos_diario_bkp_20261117`
CLONE `guara-db.guara_performance.dooki_pedidos_diario`
OPTIONS (expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 90 DAY));

DROP TABLE `guara-db.guara_performance.dooki_pedidos_diario`;
