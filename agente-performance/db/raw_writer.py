"""
db/raw_writer.py

Camada raw do bronze: guarda a resposta da API como veio, antes de qualquer normalização.
Cada fonte tem uma tabela `raw_<fonte>` em `guara_performance`, uma linha por registro da
fonte (chave natural), com o JSON original em `payload_json`. O MERGE insere o que é novo e
só atualiza quando o conteúdo mudou (`payload_hash`); a versão guardada é a mais recente.

Serve para auditar e reprocessar: se um parsing do loader estiver errado, o dado original
continua no banco. Nunca bloqueia o sync principal: falha vira aviso no log.

  salvar_raw("raw_yampi_pedidos", ["loja", "order_id"],
             [{"loja": "poddi", "order_id": "123", "payload": {...resposta da API...}}])
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PROJECT = os.environ.get("BQ_PROJECT", "guara-db")
DATASET = os.environ.get("BQ_DATASET", "guara_performance")


def _hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode()).hexdigest()


def salvar_raw(tabela: str, chaves: list[str], linhas: list[dict], client=None) -> int:
    """Upsert das linhas em `<DATASET>.<tabela>`. Retorna quantas linhas foram enviadas."""
    if not linhas:
        return 0
    try:
        from google.cloud import bigquery as bq
        client = client or bq.Client(project=PROJECT)
        alvo = f"{PROJECT}.{DATASET}.{tabela}"
        staging = f"{PROJECT}.{DATASET}._staging_{tabela}"
        agora = datetime.now(timezone.utc).isoformat()

        schema = [bq.SchemaField(k, "STRING") for k in chaves] + [
            bq.SchemaField("payload_json", "STRING"),
            bq.SchemaField("payload_hash", "STRING"),
            bq.SchemaField("capturado_em", "TIMESTAMP"),
            bq.SchemaField("atualizado_em", "TIMESTAMP"),
        ]
        client.create_table(bq.Table(alvo, schema=schema), exists_ok=True)

        registros = []
        for linha in linhas:
            payload_json = json.dumps(linha["payload"], ensure_ascii=False, sort_keys=True, default=str)
            registros.append({**{k: str(linha[k]) for k in chaves},
                              "payload_json": payload_json, "payload_hash": _hash(payload_json),
                              "capturado_em": agora, "atualizado_em": agora})
        client.load_table_from_json(
            registros, staging,
            job_config=bq.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")).result()

        on = " AND ".join(f"T.`{k}` = S.`{k}`" for k in chaves)
        cols = ", ".join(f"`{c}`" for c in [*chaves, "payload_json", "payload_hash", "capturado_em", "atualizado_em"])
        vals = ", ".join(f"S.`{c}`" for c in [*chaves, "payload_json", "payload_hash", "capturado_em", "atualizado_em"])
        client.query(f"""
            MERGE `{alvo}` T USING `{staging}` S ON {on}
            WHEN MATCHED AND T.payload_hash != S.payload_hash THEN
              UPDATE SET payload_json = S.payload_json, payload_hash = S.payload_hash,
                         atualizado_em = S.atualizado_em
            WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({vals})
        """).result()
        client.delete_table(staging, not_found_ok=True)
        logger.info("[raw] %s: %d registros", tabela, len(registros))
        return len(registros)
    except Exception as exc:  # o raw nunca derruba o sync
        logger.warning("[raw] %s falhou: %s", tabela, exc)
        return 0
