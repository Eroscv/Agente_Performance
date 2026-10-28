"""
agent/image_agent.py

Agente de Geração de Imagens do Agente de Performance Guará.

Fluxo:
  1. Recebe prompt do usuário (PT ou EN)
  2. Claude refina e traduz o prompt para EN otimizado
  3. Chama Gemini (gemini-2.5-flash-image) para gerar a imagem
  4. Retorna bytes PNG + metadados para o responder.py enviar no Slack

Provider: Gemini → GEMINI_API_KEY no .env (já configurado)
Modelos (em ordem): gemini-2.5-flash-image → gemini-3.1-flash-image → gemini-3-pro-image

Uso standalone:
  python3 -m agent.image_agent "campanha de verão para Source OF, estilo vibrante"
"""

import base64
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

GEMINI_KEY    = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Modelos Gemini para geração de imagem (em ordem de preferência)
GEMINI_IMAGE_MODELS = [
    os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
]


# ── Prompt refinement via Claude ──────────────────────────────────────────────

REFINE_SYSTEM = """Você é um especialista em engenharia de prompts para geração de imagens com IA.

Sua tarefa: receber uma descrição em português (ou inglês) de uma imagem desejada
e retornar APENAS o prompt em inglês, otimizado para modelos de geração de imagem.

Regras:
- Retorne SOMENTE o prompt em inglês, sem explicações, sem markdown
- Seja descritivo: inclua estilo, iluminação, composição, qualidade
- Máximo 200 palavras
- Adapte para contexto de marketing digital quando relevante
- Se o usuário mencionar cliente (Source OF, POD Kombucha, Botica), adapte o estilo:
  * Source OF → beleza feminina, elegante, clean, pastel tones
  * POD Kombucha → orgânico, natural, cores vibrantes, lifestyle saudável
  * Botica Ouro Preto → farmácia artesanal, natural, sofisticado, tons terrosos
"""


def _refinar_prompt(prompt_usuario: str) -> str:
    """Usa Claude Haiku para refinar e traduzir o prompt."""
    if not ANTHROPIC_KEY:
        logger.warning("[image] ANTHROPIC_API_KEY não configurada — usando prompt original")
        return prompt_usuario

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=REFINE_SYSTEM,
            messages=[{"role": "user", "content": prompt_usuario}],
        )
        refined = msg.content[0].text.strip()
        logger.info("[image] Prompt refinado: %s", refined[:80])
        return refined
    except Exception as e:
        logger.warning("[image] Falha ao refinar prompt: %s — usando original", e)
        return prompt_usuario


# ── Provider: Gemini ──────────────────────────────────────────────────────────

def _gerar_gemini(prompt_en: str) -> bytes:
    """
    Gera imagem via Gemini.
    Tenta cada modelo em GEMINI_IMAGE_MODELS até um funcionar.
    Retorna bytes PNG.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_KEY)
    last_err = None

    for model_name in GEMINI_IMAGE_MODELS:
        try:
            logger.info("[image] Gemini → modelo=%s", model_name)
            response = client.models.generate_content(
                model=model_name,
                contents=f"Generate an image: {prompt_en}",
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"]
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    raw = part.inline_data.data
                    if isinstance(raw, (bytes, bytearray)):
                        return bytes(raw)
                    return base64.b64decode(raw)
            raise ValueError(f"Gemini {model_name} não retornou imagem nos parts")
        except Exception as e:
            logger.warning("[image] Gemini %s falhou: %s", model_name, str(e)[:120])
            last_err = e

    raise RuntimeError(f"Todos os modelos Gemini falharam. Último erro: {last_err}")


# ── Interface pública ─────────────────────────────────────────────────────────

class ImagemGerada:
    """Resultado de uma geração de imagem."""
    def __init__(
        self,
        bytes_png:       bytes,
        prompt_original: str,
        prompt_refinado: str,
        provider:        str,
        filename:        str,
    ):
        self.bytes_png       = bytes_png
        self.prompt_original = prompt_original
        self.prompt_refinado = prompt_refinado
        self.provider        = provider
        self.filename        = filename
        self.size_kb         = len(bytes_png) // 1024


def gerar_imagem(
    prompt:  str,
    refinar: bool = True,
) -> ImagemGerada:
    """
    Interface principal: recebe prompt PT/EN, refina com Claude,
    gera imagem via Gemini e retorna ImagemGerada.
    """
    if not GEMINI_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY não configurada no .env"
        )

    prompt_original = prompt.strip()
    prompt_en = _refinar_prompt(prompt_original) if refinar else prompt_original

    slug = re.sub(r"[^a-z0-9]+", "_", prompt_en.lower())[:40].strip("_")

    img_bytes = _gerar_gemini(prompt_en)
    return ImagemGerada(
        bytes_png=img_bytes,
        prompt_original=prompt_original,
        prompt_refinado=prompt_en,
        provider="Gemini",
        filename=f"imagem_{slug}.png",
    )


# ── Standalone ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    prompt = " ".join(sys.argv[1:]) or "campanha de verão, produto de beleza natural, estilo clean e elegante"
    print(f"Gerando imagem para: {prompt!r}")

    result = gerar_imagem(prompt)
    out = Path(f"/tmp/{result.filename}")
    out.write_bytes(result.bytes_png)

    print(f"✅ Salvo em: {out} ({result.size_kb} KB)")
    print(f"   Prompt refinado: {result.prompt_refinado[:100]}")
