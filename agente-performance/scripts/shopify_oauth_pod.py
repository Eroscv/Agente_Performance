"""
shopify_oauth_pod.py

Gerencia o token OAuth da loja POD Kombucha (d5bead-1b.myshopify.com).

Como funciona:
  - Tokens OAuth da Shopify são "offline" por padrão (não expiram)
  - Mas apps customizados no Partners podem emitir tokens "online" (expiram em 24h)
  - Este script gera um novo token via Authorization Code Grant
  - Atualiza o .env automaticamente após gerar

Uso:
  python3 shopify_oauth_pod.py --gerar-url   # gera URL de autorização para colar no browser
  python3 shopify_oauth_pod.py --trocar CODE  # troca o code pelo access token
  python3 shopify_oauth_pod.py --status       # verifica se token atual funciona
"""

import argparse
import hashlib
import hmac
import os
import re
import sys
import requests
from pathlib import Path
from urllib.parse import urlencode

# ── Config ────────────────────────────────────────────────────────────────────
STORE        = "d5bead-1b.myshopify.com"
CLIENT_ID    = "5fabbf799363294310469c9e7db731f1"
CLIENT_SECRET= "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"
REDIRECT_URI = "https://guara.com.br/callback"
SCOPES       = "read_orders,write_orders,read_products,write_products"
ENV_FILE     = Path(__file__).resolve().parent.parent / ".env"
ENV_KEY      = "SHOPIFY_POD_TOKEN"

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_current_token() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith(f"{ENV_KEY}="):
            return line.split("=", 1)[1].strip()
    return ""


def update_env_token(new_token: str):
    """Substitui SHOPIFY_POD_TOKEN no .env."""
    content = ENV_FILE.read_text()
    new_content = re.sub(
        rf"^{ENV_KEY}=.*$",
        f"{ENV_KEY}={new_token}",
        content,
        flags=re.MULTILINE,
    )
    if new_content == content:
        # Linha não existia — adiciona ao final
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
    url = f"https://{STORE}/admin/oauth/authorize?{urlencode(params)}"
    return url


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
    """Gera novo token via client_credentials (expira em ~24h)."""
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
    print(f"Token gerado — expira em {expires_in//3600}h")
    return token


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gerar-url",  action="store_true", help="Gera URL de autorização OAuth")
    parser.add_argument("--trocar",     metavar="CODE",       help="Troca authorization code pelo token")
    parser.add_argument("--status",     action="store_true", help="Verifica token atual")
    args = parser.parse_args()

    if args.status:
        token = get_current_token()
        if not token:
            print("❌ Nenhum token configurado no .env")
            sys.exit(1)
        print(f"Token atual: {token[:20]}...")
        ok = test_token(token)
        if ok:
            print("✅ Token funcionando — orders acessíveis")
        else:
            print("❌ Token inválido ou expirado — renovando automaticamente...")
            try:
                new_token = renovar_token()
                ok2 = test_token(new_token)
                if ok2:
                    update_env_token(new_token)
                    print("✅ Token renovado e salvo no .env!")
                    sys.exit(0)
                else:
                    print("❌ Token renovado mas não passou no teste")
                    sys.exit(1)
            except Exception as e:
                print(f"❌ Falha na renovação: {e}")
                sys.exit(1)
        sys.exit(0 if ok else 1)

    if args.gerar_url:
        url = gerar_url_autorizacao()
        print("🔗 Acesse essa URL no browser (logado na loja do POD):")
        print()
        print(url)
        print()
        print("Após autorizar, você será redirecionado para uma URL com ?code=XXXX")
        print("Copie o CODE e rode:")
        print(f"  python3 shopify_oauth_pod.py --trocar SEU_CODE_AQUI")
        return

    if args.trocar:
        code = args.trocar
        print(f"Trocando code por token...")
        token = trocar_code_por_token(code)
        print(f"Token gerado: {token[:20]}...")

        # Testa antes de salvar
        ok = test_token(token)
        if ok:
            update_env_token(token)
            print("✅ Token válido e salvo no .env!")
        else:
            print(f"⚠️  Token gerado mas não passou no teste: {token}")
            print("Salve manualmente se quiser.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
