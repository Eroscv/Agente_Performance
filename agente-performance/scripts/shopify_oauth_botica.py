"""
shopify_oauth_botica.py

Gerencia o token OAuth da loja Botica Ouro Preto (00mngb-he.myshopify.com).

Tokens "online" expiram em 24h — este script renova automaticamente.

Uso:
  python3 shopify_oauth_botica.py --gerar-url   # gera URL de autorização
  python3 shopify_oauth_botica.py --trocar CODE  # troca code pelo access token
  python3 shopify_oauth_botica.py --status       # verifica/renova token atual
"""

import argparse
import os
import re
import sys
import requests
from pathlib import Path
from urllib.parse import urlencode

# ── Config ────────────────────────────────────────────────────────────────────
STORE         = "00mngb-he.myshopify.com"
CLIENT_ID     = "57f90faf153f67626703ae5a7422233e"
CLIENT_SECRET = "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"
REDIRECT_URI  = "https://guara.com.br/callback"
SCOPES        = "read_orders,read_products,read_analytics"
ENV_FILE      = Path(__file__).parent.parent / ".env"
ENV_KEY       = "SHOPIFY_BOTICA_TOKEN"

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_current_token() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(f"{ENV_KEY}="):
            return line.split("=", 1)[1].strip()
    return ""


def update_env_token(new_token: str):
    """Substitui SHOPIFY_BOTICA_TOKEN no .env."""
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
    """Testa se o token funciona na loja."""
    r = requests.get(
        f"https://{STORE}/admin/api/2024-01/orders.json?limit=1&status=any",
        headers={"X-Shopify-Access-Token": token},
        timeout=10,
    )
    return r.status_code == 200


def gerar_url_autorizacao() -> str:
    """Gera a URL OAuth para autorizar o app na loja."""
    params = {
        "client_id": CLIENT_ID,
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
    }
    return f"https://{STORE}/admin/oauth/authorize?{urlencode(params)}"


def trocar_code_por_token(code: str) -> str:
    """Troca o authorization code pelo access token."""
    r = requests.post(
        f"https://{STORE}/admin/oauth/access_token",
        json={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Erro ao trocar code: {r.status_code} — {r.text}")
    data = r.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Token não retornado: {data}")
    return token


def renovar_token() -> str:
    """Gera novo token via client_credentials."""
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
        raise RuntimeError(f"Erro ao renovar: {r.status_code} — {r.text}")
    data = r.json()
    token = data.get("access_token")
    expires_in = data.get("expires_in", 0)
    if not token:
        raise RuntimeError(f"Token não retornado: {data}")
    print(f"Token gerado — expira em {expires_in // 3600}h")
    return token


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gerar-url", action="store_true", help="Gera URL de autorização OAuth")
    parser.add_argument("--trocar",    metavar="CODE",      help="Troca authorization code pelo token")
    parser.add_argument("--status",    action="store_true", help="Verifica/renova token atual")
    args = parser.parse_args()

    if args.status:
        token = get_current_token()
        if not token:
            print("❌ Nenhum token configurado no .env")
            sys.exit(1)
        print(f"Token atual: {token[:20]}...")
        ok = test_token(token)
        if ok:
            print("✅ Token funcionando")
            sys.exit(0)
        else:
            print("❌ Token inválido ou expirado — renovando...")
            try:
                new_token = renovar_token()
                if test_token(new_token):
                    update_env_token(new_token)
                    print("✅ Token renovado e salvo!")
                    sys.exit(0)
                else:
                    print("❌ Token renovado mas não passou no teste")
                    sys.exit(1)
            except Exception as e:
                print(f"❌ Falha na renovação: {e}")
                sys.exit(1)

    elif args.gerar_url:
        url = gerar_url_autorizacao()
        print("🔗 Acesse essa URL no browser (logado na loja da Botica):")
        print()
        print(url)
        print()
        print("Após autorizar, copie o CODE da URL e rode:")
        print(f"  python3 shopify_oauth_botica.py --trocar SEU_CODE_AQUI")

    elif args.trocar:
        print("Trocando code por token...")
        token = trocar_code_por_token(args.trocar)
        print(f"Token gerado: {token[:20]}...")
        if test_token(token):
            update_env_token(token)
            print("✅ Token válido e salvo no .env!")
        else:
            print(f"⚠️  Token gerado mas não passou no teste: {token}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
