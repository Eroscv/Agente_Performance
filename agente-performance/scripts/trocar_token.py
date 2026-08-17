"""Troca o authorization code pelo refresh token."""
import urllib.request
import urllib.parse
import json

CLIENT_ID     = "959916584652-kl4jteb7qhsgf77lsbnd2h2v5tdod6vt.apps.googleusercontent.com"
CLIENT_SECRET = "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"
CODE          = "4/0ATsMZqBLQYriHw6JeQm189finkcD6DkHRDAS652n4BUTLns2L9tQpmw15UawEfWpr2jBGw"
REDIRECT_URI  = "http://localhost"

data = urllib.parse.urlencode({
    "code": CODE,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "redirect_uri": REDIRECT_URI,
    "grant_type": "authorization_code",
}).encode()

req = urllib.request.Request(
    "https://oauth2.googleapis.com/token",
    data=data,
    method="POST",
)

with urllib.request.urlopen(req) as resp:
    result = json.loads(resp.read())

print(json.dumps(result, indent=2))
