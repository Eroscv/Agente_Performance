"""Carga do BigQuery simulando falha: nova tentativa automática e aviso por cliente e fonte.

Roda o cron_master_sync.sh de verdade numa cópia, com os syncs trocados por scripts falsos e curl falso.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

ETAPAS = [
    "scripts/shopify_oauth_pod.py", "scripts/shopify_oauth_botica.py", "scripts/shopify_oauth_source_of.py",
    "scripts/cron_shopify_sync.py", "scripts/sync_pedidos_diario.py", "scripts/sync_meta_diario.py",
    "dooki/sync.py", "scripts/sync_shopify_clientes_historico.py", "scripts/sync_meta_semanal.py",
    "scripts/rebuild_bq_layers.py", "scripts/comparar_bq_api.py",
]

# Conta as chamadas e registra o resultado por cliente no sync_alerts de verdade.
FALSO = '''import sys, pathlib
base = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(base))
from db.sync_alerts import registrar_resultado
cont = base / "chamadas_{nome}"
n = int(cont.read_text()) + 1 if cont.exists() else 1
cont.write_text(str(n))
{corpo}
'''

GOOGLE = '''registrar_resultado("pod_kombucha", "google", sucesso=True)
registrar_resultado("source_of", "google", sucesso=False, erro='token "expirado"\\nlinha 2')
sys.exit(1)'''

META = '''if n == 1:
    registrar_resultado("pod_kombucha", "meta", sucesso=False, erro="timeout")
    sys.exit(1)
registrar_resultado("pod_kombucha", "meta", sucesso=True)'''


def _carga(d: Path):
    (d / "scripts").mkdir()
    (d / "dooki").mkdir()
    (d / "db").mkdir()
    shutil.copy(RAIZ / "scripts/cron_master_sync.sh", d / "scripts")
    shutil.copy(RAIZ / "db/sync_alerts.py", d / "db")
    (d / "db/__init__.py").write_text("")
    (d / "dooki/__init__.py").write_text("")
    for e in ETAPAS:
        (d / e).write_text(FALSO.format(nome=Path(e).stem, corpo=""))
    (d / "scripts/sync_gads_diario.py").write_text(FALSO.format(nome="gads", corpo=GOOGLE))
    (d / "scripts/sync_meta_diario.py").write_text(FALSO.format(nome="meta", corpo=META))
    (d / ".env").write_text("SLACK_BOT_TOKEN=xoxb-teste\nSLACK_ALERT_CHANNEL=C0TESTE\n")
    bin_ = d / "bin"
    bin_.mkdir()
    (bin_ / "curl").write_text('#!/usr/bin/env bash\nwhile [ $# -gt 0 ]; do [ "$1" = --data ] && echo "$2" >> "$SAIDA"; shift; done\n')
    (bin_ / "flock").write_text("#!/bin/sh\nexit 0\n")  # macOS não tem flock
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "SAIDA": str(d / "slack"),
           "SYNC_TENTATIVAS": "2", "SYNC_ESPERA_S": "0"}
    env.pop("SLACK_BOT_TOKEN", None)
    rc = subprocess.run(["bash", str(d / "scripts/cron_master_sync.sh")], env=env).returncode
    return rc, (d / "slack").read_text() if (d / "slack").exists() else ""


def test_falha_tenta_de_novo_e_avisa_por_cliente_e_fonte():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        rc, slack = _carga(d)
        assert rc == 1
        assert (d / "chamadas_gads").read_text() == "2"    # nova tentativa automática
        assert (d / "chamadas_meta").read_text() == "2"    # falhou 1x, passou na 2ª
        assert slack.count("\n") == 1, slack               # um aviso só (o sync_alerts não posta dentro da carga)
        assert "• Sync Google Ads" in slack
        assert "• source_of / google: token 'expirado' linha 2" in slack
        assert "pod_kombucha / google" not in slack        # cliente que passou não aparece
        assert "Sync Meta Ads" not in slack and "pod_kombucha / meta" not in slack  # recuperado na 2ª tentativa
        import json
        json.loads(slack)                                   # aspas e quebras do erro não quebram o JSON


def test_sync_alerts_fora_da_carga_ainda_avisa():
    from db import sync_alerts
    with mock.patch.dict(os.environ, {"SLACK_BOT_TOKEN": "x"}), mock.patch("requests.post") as post:
        os.environ.pop("SYNC_ALERT_SLACK", None)
        sync_alerts._enviar_alerta_slack("c", "meta", "erro", 2)
        assert post.called
        post.reset_mock()
        os.environ["SYNC_ALERT_SLACK"] = "0"
        sync_alerts._enviar_alerta_slack("c", "meta", "erro", 2)
        assert not post.called
        os.environ.pop("SYNC_ALERT_SLACK")


if __name__ == "__main__":
    test_falha_tenta_de_novo_e_avisa_por_cliente_e_fonte()
    test_sync_alerts_fora_da_carga_ainda_avisa()
    print("ok")
