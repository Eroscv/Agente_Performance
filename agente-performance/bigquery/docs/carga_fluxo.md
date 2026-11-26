# Carga do BigQuery na VM: fluxo, nova tentativa e alerta

`guara-carga-bq.timer` roda `scripts/cron_master_sync.sh` a cada 3h (02:10 UTC + 3h).

```mermaid
flowchart TD
    T[guara-carga-bq.timer<br/>a cada 3h] --> L{carga anterior<br/>ainda rodando?}
    L -- sim --> FIM0[sai sem fazer nada]
    L -- não --> E["próxima etapa<br/>tokens Shopify → Shopify → pedidos → Google → Meta<br/>→ Yampi → clientes POD → Meta semanal* → camadas → comparação*"]
    E --> C[sync roda cliente por cliente]
    C --> R[registra cliente/fonte<br/>db/sync_alerts + ingestao.log_cargas_fontes]
    R --> Q{algum cliente<br/>falhou?}
    Q -- não --> OK[etapa OK]
    Q -- sim --> N{"ainda há tentativa?<br/>SYNC_TENTATIVAS = 3"}
    N -- sim --> W["espera SYNC_ESPERA_S = 120 s"] --> C
    N -- não --> F[etapa entra na lista de falhas<br/>e a carga segue]
    OK --> M{mais etapas?}
    F --> M
    M -- sim --> E
    M -- não --> S{houve falha?}
    S -- não --> AV1[Slack #alertas-tecnicos: carga OK<br/>+ etapas que passaram na nova tentativa]
    S -- sim --> AV2[Slack #alertas-tecnicos: etapas com falha<br/>+ cliente / fonte / erro que seguiram falhando<br/>exit 1 → post de saúde]
```

\* Meta semanal só na segunda; comparação BigQuery × API uma vez por semana.

## Regras

- **Nova tentativa:** a etapa inteira é refeita quando qualquer cliente falha (todos os syncs saem com código 1 nesse caso).
- **Fonte por etapa:** `shopify`, `pedidos`, `google`, `meta`, `yampi` (por loja), `shopify_clientes`.
- **Alerta por cliente e fonte:** dentro da carga o `sync_alerts` não posta (`SYNC_ALERT_SLACK=0`); o aviso sai um só, no fim, listando quem seguiu em erro depois das tentativas. Rodando um sync avulso, o `sync_alerts` avisa sozinho (2 falhas seguidas, 1h de intervalo).
- **Teste:** `tests/test_carga_falha.py` roda o script de verdade com syncs falsos (um cliente sempre falha, outro só na 1ª tentativa) e confere a nova tentativa e o texto do aviso.
