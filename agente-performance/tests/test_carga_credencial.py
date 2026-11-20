"""20/11/2026: na VM a credencial do BigQuery vem do .env; lida antes do load_dotenv, o Dooki caía no caminho do notebook."""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def test_credencial_lida_depois_do_dotenv():
    for f in ("dooki/sync.py", "scripts/sync_shopify_clientes_historico.py"):
        fonte = (RAIZ / f).read_text()
        dotenv = re.search(r"^load_dotenv\(", fonte, re.M).start()
        cred = re.search(r"^BQ_CREDS\s*=.*GOOGLE_APPLICATION_CREDENTIALS", fonte, re.M).start()
        assert dotenv < cred, f
