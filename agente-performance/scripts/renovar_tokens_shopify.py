"""
scripts/renovar_tokens_shopify.py — Renova os tokens da Shopify (client_credentials, ~24h) no .env.

Os apps das lojas emitem token que expira em ~24h; sem renovação, todo sync da Shopify dá 401.
Roda pelo timer guara-shopify-token (2x/dia). Quem lê o .env a cada execução (guara-job@) pega o
token novo sozinho; o bot é reiniciado pelo ExecStartPost do drop-in do timer.
ponytail: reinício do bot 2x/dia; se incomodar, trocar por leitura do token em tempo de execução.

Exit 1 se alguma loja falhar (a unit aparece como falha).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
ENV_FILE = BASE_DIR / ".env"


def _lojas() -> list[tuple[str, str, str, str]]:
    """(rótulo, loja, client_id, client_secret, chave do token no .env)"""
    from scripts import shopify_oauth_botica as bo, shopify_oauth_pod as po
    so_loja = os.getenv("SHOPIFY_STORE", "source-of-brasil").removesuffix(".myshopify.com") + ".myshopify.com"
    return [
        ("source_of", so_loja, os.getenv("SHOPIFY_SOURCEOF_CLIENT_ID", ""),
         os.getenv("SHOPIFY_SOURCEOF_CLIENT_SECRET", ""), "SHOPIFY_TOKEN"),
        ("pod_kombucha", po.STORE, po.CLIENT_ID, po.CLIENT_SECRET, po.ENV_KEY),
        ("botica", bo.STORE, bo.CLIENT_ID, bo.CLIENT_SECRET, bo.ENV_KEY),
    ]


def gravar(texto_env: str, chave: str, valor: str) -> str:
    linha = f"{chave}={valor}"
    novo, n = re.subn(rf"^#?\s*{re.escape(chave)}=.*$", lambda _: linha, texto_env, count=1, flags=re.M)
    return novo if n else texto_env.rstrip("\n") + f"\n{linha}\n"


def main() -> int:
    texto, falhas = ENV_FILE.read_text(), []
    for rotulo, loja, cid, segredo, chave in _lojas():
        if not (cid and segredo):
            falhas.append(f"{rotulo}: client_id/secret ausentes")
            continue
        try:
            r = requests.post(f"https://{loja}/admin/oauth/access_token", timeout=20, json={
                "client_id": cid, "client_secret": segredo, "grant_type": "client_credentials"})
            r.raise_for_status()
            token = r.json()["access_token"]
            requests.get(f"https://{loja}/admin/api/2024-01/shop.json", timeout=20,
                         headers={"X-Shopify-Access-Token": token}).raise_for_status()
        except Exception as e:
            falhas.append(f"{rotulo}: {str(e)[:150]}")
            continue
        texto = gravar(texto, chave, token)
        print(f"[{rotulo}] token renovado (expira em ~{r.json().get('expires_in', 0) // 3600}h)")
    ENV_FILE.write_text(texto)
    ENV_FILE.chmod(0o600)
    for f in falhas:
        print(f"❌ {f}", file=sys.stderr)
    return 1 if falhas else 0


if __name__ == "__main__":
    assert gravar("A=1\nT=velho\n", "T", "novo") == "A=1\nT=novo\n"
    assert gravar("A=1", "T", "x") == "A=1\nT=x\n"
    assert gravar("# T=\n", "T", "x") == "T=x\n"
    sys.exit(main())
