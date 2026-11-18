"""
utils/llm.py — chamada única de texto ao LLM para todo o repo (bot, relatórios, transcrição, LPs).

Primário: Gemini (GEMINI_API_KEY, depois GEMINI_API_KEY_BACKUP), modelo GEMINI_MODEL.
Fallback: Claude, só se ANTHROPIC_API_KEY existir.

    from utils.llm import gerar
    texto = gerar(system, mensagem, max_tokens=1024)
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

MODELO_PADRAO = "gemini-3.8-flash"
PISO_TOKENS_GEMINI = 4096
_clientes: dict = {}


def _gemini(system: str, user: str, max_tokens: int, temperature: float, modelo: str | None) -> str:
    from google import genai
    from google.genai import types

    modelo = modelo or os.getenv("GEMINI_MODEL", MODELO_PADRAO)
    chaves = [k for k in (os.getenv("GEMINI_API_KEY", "").strip(), os.getenv("GEMINI_API_KEY_BACKUP", "").strip()) if k]
    if not chaves:
        raise RuntimeError("GEMINI_API_KEY não configurada")
    erro = None
    for chave in chaves:
        try:
            # o Client precisa ficar referenciado: temporário é coletado e fecha a conexão no meio
            cli = _clientes.get(chave) or _clientes.setdefault(chave, genai.Client(api_key=chave))
            r = cli.models.generate_content(
                model=modelo,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system or None,
                    # o raciocínio do Gemini 3.x conta neste limite: 300 tokens cortava a resposta no meio
                    max_output_tokens=max(max_tokens, PISO_TOKENS_GEMINI),
                    temperature=temperature,
                ),
            )
            if not r.text:  # bloqueio de segurança ou orçamento de tokens gasto no raciocínio
                raise RuntimeError(f"Gemini devolveu vazio (finish={r.candidates[0].finish_reason if r.candidates else '?'})")
            return r.text
        except Exception as e:
            erro = e
            logger.warning("[LLM] Gemini falhou (%s...): %s", chave[:6], e)
    raise RuntimeError(f"Gemini indisponível: {erro}")


def _claude(system: str, user: str, max_tokens: int, temperature: float) -> str:
    import anthropic

    chave = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not chave:
        raise RuntimeError("ANTHROPIC_API_KEY não configurada")
    r = anthropic.Anthropic(api_key=chave).messages.create(
        model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.content[0].text


def gerar(system: str, user: str, max_tokens: int = 1024, temperature: float = 0.3,
          modelo: str | None = None) -> str:
    """Texto do LLM (Gemini → Claude). Levanta RuntimeError se nenhum responder."""
    try:
        return _gemini(system, user, max_tokens, temperature, modelo).strip()
    except Exception as eg:
        msg_g = str(eg)
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        raise RuntimeError(f"LLM indisponível. Gemini: {msg_g}")
    try:
        return _claude(system, user, max_tokens, temperature).strip()
    except Exception as ea:
        raise RuntimeError(f"Todos os LLMs indisponíveis. Gemini: {msg_g} | Claude: {ea}")
