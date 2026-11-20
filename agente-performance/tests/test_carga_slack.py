"""Aviso da carga do BigQuery: posta em SLACK_ALERT_CHANNEL com o bot, sem expor o token no argv."""
import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cron_master_sync.sh"


def _rodar(env_extra, dotenv=""):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / ".env").write_text(dotenv)
        # curl falso: grava argv e o conteúdo dos headers lidos de arquivo/fd
        (d / "curl").write_text(
            '#!/usr/bin/env bash\n'
            'for a in "$@"; do echo "ARG $a"; case "$a" in @*) echo "HDR $(cat "${a#@}")";; esac; done > "$SAIDA"\n'
        )
        (d / "curl").chmod(0o755)
        bash = (
            f'BASE_DIR={d}; LOG_FILE={d}/log; '
            f'eval "$(sed -n "/^env_var()/,/^}}/p; /^avisar_slack()/,/^}}/p" {SCRIPT})"; '
            'avisar_slack "oi\\\\nlinha 2"'
        )
        env = {"PATH": f"{d}:{os.environ['PATH']}", "SAIDA": str(d / "saida"), **env_extra}
        subprocess.run(["bash", "-c", bash], env=env, check=True)
        return (d / "saida").read_text() if (d / "saida").exists() else None


def test_posta_no_canal_de_alertas_com_bot():
    out = _rodar({}, "SLACK_BOT_TOKEN=xoxb-teste\nSLACK_ALERT_CHANNEL=C0CHLBV0SBT\n")
    assert "ARG https://slack.com/api/chat.postMessage" in out
    assert '"channel":"C0CHLBV0SBT"' in out
    assert "HDR Authorization: Bearer xoxb-teste" in out
    assert not any(l.startswith("ARG") and "xoxb-teste" in l for l in out.splitlines())


def test_sem_token_nao_posta():
    assert _rodar({}, "SLACK_ALERT_CHANNEL=C0CHLBV0SBT\n") is None


if __name__ == "__main__":
    test_posta_no_canal_de_alertas_com_bot()
    test_sem_token_nao_posta()
    print("ok")
