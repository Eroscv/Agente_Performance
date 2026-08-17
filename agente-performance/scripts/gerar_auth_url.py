"""Gera URL de autorização OAuth usando localhost redirect."""
import urllib.parse

CLIENT_ID = "959916584652-kl4jteb7qhsgf77lsbnd2h2v5tdod6vt.apps.googleusercontent.com"
SCOPE     = "https://www.googleapis.com/auth/adwords"

params = {
    "response_type": "code",
    "client_id": CLIENT_ID,
    "redirect_uri": "http://localhost",
    "scope": SCOPE,
    "access_type": "offline",
    "prompt": "consent",
}

url = "https://accounts.google.com/o/oauth2/auth?" + urllib.parse.urlencode(params)
print(url)
