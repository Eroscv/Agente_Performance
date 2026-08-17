"""
scripts/gerar_refresh_token.py

Gera o Refresh Token OAuth2 para a Google Ads API.

Uso:
    python scripts/gerar_refresh_token.py

1. Abre a URL no browser
2. Autoriza a conta Google que tem acesso ao Google Ads
3. Cola o código aqui
4. O Refresh Token é exibido no terminal
"""

import sys
from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_ID     = "959916584652-kl4jteb7qhsgf77lsbnd2h2v5tdod6vt.apps.googleusercontent.com"
CLIENT_SECRET = "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"

SCOPES = ["https://www.googleapis.com/auth/adwords"]

client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

flow = InstalledAppFlow.from_client_config(
    client_config,
    scopes=SCOPES,
    redirect_uri="urn:ietf:wg:oauth:2.0:oob",
)

auth_url, _ = flow.authorization_url(
    access_type="offline",
    prompt="consent",
)

print("\n" + "="*60)
print("  AUTORIZAÇÃO GOOGLE ADS")
print("="*60)
print("\n1. Abre esta URL no browser:\n")
print(f"   {auth_url}")
print("\n2. Faz login com a conta que tem acesso ao Google Ads")
print("3. Autoriza o app")
print("4. Copia o código que aparecer na tela")
print("\n" + "="*60)

code = input("\nCola o código aqui: ").strip()

flow.fetch_token(code=code)
creds = flow.credentials

print("\n" + "="*60)
print("  ✅ REFRESH TOKEN GERADO COM SUCESSO")
print("="*60)
print(f"\n  GOOGLE_ADS_REFRESH_TOKEN={creds.refresh_token}\n")
print("="*60)
print("\nGuarda esse valor — você vai precisar dele uma única vez.\n")
