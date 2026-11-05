"""
shopify_oauth_source_of.py

Gerencia o token OAuth da loja Source Of (source-of-brasil.myshopify.com).

Mesma lógica de shopify_oauth_pod.py / shopify_oauth_botica.py, mas lê
CLIENT_ID/CLIENT_SECRET do .env (SHOPIFY_SOURCEOF_CLIENT_ID/SECRET) em vez
de hardcodear, já que o app foi criado depois.

Uso:
  python3 scripts/shopify_oauth_source_of.py --status    # testa e renova se preciso
  python3 scripts/shopify_oauth_source_of.py --renovar   # força renovação
"""

import argparse
import os
import re
import sys
import requests
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

# ── Config ────────────────────────────────────────────────────────────────────
STORE         = "source-of-brasil.myshopify.com"
CLIENT_ID     = os.environ.get("SHOPIFY_SOURCEOF_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("SHOPIFY_SOURCEOF_CLIENT_SECRET", "").strip()
ENV_FILE      = BASE_DIR / ".env"
ENV_KEY       = "SHOPIFY_TOKEN"

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_current_token() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(f"{ENV_KEY}="):
            return line.split("=", 1)[1].strip()
    return ""


def update_env_token(new_token: str):
    content = ENV_FILE.read_text()
    new_content = re.sub(
        rf"^{ENV_KEY}=.*$",
        f"{ENV_KEY}={new_token}",
        content,
        flags=re.MULTILINE,
    )
    if new_content == content:
        new_content = content.rstrip() + f"\n{ENV_KEY}={new_token}\n"
    ENV_FILE.write_text(new_content)
    print(f"✅ .env atualizado: {ENV_KEY}={new_token[:20]}...")


def test_token(token: str) -> bool:
    r = requests.get(
        f"https://{STORE}/admin/api/2025-01/orders.json?limit=1&status=any",
        headers={"X-Shopify-Access-Token": token},
        timeout=10,
    )
    return r.status_code == 200


def renovar_token() -> str:
    """Gera novo token via client_credentials."""
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError(
            "SHOPIFY_SOURCEOF_CLIENT_ID / SHOPIFY_SOURCEOF_CLIENT_SECRET não "
            "configurados no .env"
        )
    r = requests.post(
        f"https://{STORE}/admin/oauth/access_token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Erro ao renovar token: {r.status_code} — {r.text}")
    data = r.json()
    token = data.get("access_token")
    expires_in = data.get("expires_in", 0)
    if not token:
        raise RuntimeError(f"Token não retornado: {data}")
    print(f"Token gerado — expira em {expires_in//3600}h" if expires_in else "Token gerado (offline, sem expiração)")
    return token


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status",  action="store_true", help="Testa token atual, renova se preciso")
    parser.add_argument("--renovar", action="store_true", help="Força renovação")
    args = parser.parse_args()

    if args.renovar:
        new_token = renovar_token()
        if test_token(new_token):
            update_env_token(new_token)
            print("✅ Token renovado e salvo no .env!")
            sys.exit(0)
        else:
            print("❌ Token renovado mas não passou no teste")
            sys.exit(1)

    if args.status:
        token = get_current_token()
        ok = test_token(token) if token else False
        if ok:
            print("✅ Token funcionando — orders acessíveis")
            sys.exit(0)
        print("❌ Token inválido/expirado ou ausente — renovando automaticamente...")
        try:
            new_token = renovar_token()
            if test_token(new_token):
                update_env_token(new_token)
                print("✅ Token renovado e salvo no .env!")
                sys.exit(0)
            print("❌ Token renovado mas não passou no teste")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Falha na renovação: {e}")
            sys.exit(1)

    parser.print_help()


if __name__ == "__main__":
    main()
