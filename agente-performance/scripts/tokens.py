"""
scripts/tokens.py — Vencimento das credenciais de terceiros (AUD-09). Roda 1x/dia (guara-tokens.timer).

Uma linha por credencial; nunca imprime o valor:
  • Meta (cada *_ACCESS_TOKEN do clientes.json): debug_token → is_valid, expires_at, data_access_expires_at
  • Google Ads: uma chamada mínima (listAccessibleCustomers) com o refresh token
  • Slack: auth.test
Credencial inválida = falha já (exit 1: a rotina aparece vermelha no post de saúde); vencendo em ≤ 7 dias =
aviso (alerta, exit 0). Alerta em SLACK_ALERT_CHANNEL, 1x/dia por conjunto de problemas.
A Shopify fica de fora: renovar_tokens_shopify.py já renova e falha alto.
ponytail: idade da chave da conta de serviço do BigQuery não é checada (exige IAM); entra quando houver rotação.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

AVISO_DIAS = 7


def tokens_meta() -> list[str]:
    cfg = json.loads((BASE_DIR / "clientes.json").read_text())["clientes"]
    nomes = {"META_ACCESS_TOKEN"}
    for c in cfg.values():
        m = c.get("plataformas", {}).get("meta_ads", {})
        if c.get("ativo") and m.get("ativo"):
            nomes |= {m[k] for k in ("meta_token_env", "meta_token_env2") if m.get(k)}
    return sorted(nomes)


def checar_meta(nome: str) -> tuple[str, str]:
    """('ok'|'aviso'|'falha', detalhe)."""
    tk = os.getenv(nome)
    if not tk:
        return "falha", "vazio no .env"
    r = requests.get("https://graph.facebook.com/v20.0/debug_token", timeout=30,
                     params={"input_token": tk, "access_token": tk})
    d = r.json().get("data", {}) if r.ok else {}
    if not d.get("is_valid"):
        return "falha", "inválido ou revogado (" + str(d.get("error", {}).get("message") or r.status_code)[:80] + ")"
    venc = [v for v in (d.get("expires_at"), d.get("data_access_expires_at")) if v]  # 0 = não expira
    if not venc:
        return "ok", "sem vencimento"
    dias = (min(venc) - time.time()) / 86400
    return ("aviso" if dias <= AVISO_DIAS else "ok"), f"vence em {dias:.0f} dia(s)"


def checar_gads() -> tuple[str, str]:
    from google_ads.client import get_client
    get_client().get_service("CustomerService").list_accessible_customers()
    return "ok", "refresh token aceito"


def checar_slack() -> tuple[str, str]:
    r = requests.post("https://slack.com/api/auth.test", timeout=20,
                      headers={"Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN', '')}"}).json()
    return ("ok", "token aceito") if r.get("ok") else ("falha", f"recusado ({r.get('error')})")


def verificar() -> list[tuple[str, str, str]]:
    checagens = [(f"Meta {n}", lambda n=n: checar_meta(n)) for n in tokens_meta()]
    checagens += [("Google Ads", checar_gads), ("Slack", checar_slack)]
    out = []
    for nome, f in checagens:
        try:
            st, det = f()
        except Exception as e:  # erro de rede ou credencial recusada: falha, com o tipo e não o valor
            st, det = "falha", f"{type(e).__name__}: {str(e)[:80]}"
        out.append((nome, st, det))
    return out


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
    res = verificar()
    for nome, st, det in res:
        print(f"{st:5}  {nome}: {det}")
    ruins = [r for r in res if r[1] != "ok"]
    if not ruins:
        return 0
    from scripts.qualidade_dados import _alertar
    from datetime import date
    texto = ":key: *Credenciais*: " + "; ".join(f"{n} {d}" for n, _, d in ruins)
    _alertar(texto, f"tokens:{date.today()}:{hashlib.sha1(texto.encode()).hexdigest()[:12]}")
    return 1 if any(st == "falha" for _, st, _ in ruins) else 0


if __name__ == "__main__":
    sys.exit(main())
