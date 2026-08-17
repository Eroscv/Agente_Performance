"""
google_ads/client.py

Inicializa o cliente autenticado da Google Ads API.
Lê credenciais do .env automaticamente.
"""

import os
from google.ads.googleads.client import GoogleAdsClient as _GAClient
from dotenv import load_dotenv

load_dotenv()

_YAML_TEMPLATE = """
developer_token: {developer_token}
client_id: {client_id}
client_secret: {client_secret}
refresh_token: {refresh_token}
login_customer_id: {login_customer_id}
use_proto_plus: True
"""


def get_client() -> _GAClient:
    """Retorna cliente autenticado Google Ads API."""
    yaml_str = _YAML_TEMPLATE.format(
        developer_token=os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
        client_id=os.environ["GOOGLE_ADS_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_ADS_CLIENT_SECRET"],
        refresh_token=os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
        login_customer_id=os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"],
    )
    return _GAClient.load_from_string(yaml_str)
