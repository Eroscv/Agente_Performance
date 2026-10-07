"""
agent/tz.py

Funções utilitárias de data/hora no fuso BRT (America/Sao_Paulo, UTC-3).
Use sempre estas funções no agente — nunca date.today() ou datetime.utcnow() diretamente.
"""

from datetime import date, datetime, timezone, timedelta

BRT = timezone(timedelta(hours=-3), name="BRT")


def hoje_brt() -> date:
    """Retorna a data atual em BRT."""
    return datetime.now(tz=BRT).date()


def agora_brt() -> datetime:
    """Retorna o datetime atual com tz=BRT."""
    return datetime.now(tz=BRT)


def agora_iso() -> str:
    """Retorna o datetime atual em BRT como string ISO 8601."""
    return agora_brt().isoformat()
