"""
agent/image_agent.py

Agente de Geração de Imagens do Agente de Performance Guará.

Fluxo:
  1. Recebe prompt do usuário (PT ou EN)
  2. Claude refina e traduz o prompt para EN otimizado
  3. Chama provider de imagem (fal.ai FLUX por padrão, Stability fallback)
  4. Retorna bytes PNG + metadados para o responder.py enviar no Slack

Providers suportados (ordem de prioridade):
  1. Gemini       → GEMINI_API_KEY no .env   (gemini-2.5-flash-image / 3-pro-image)
  2. fal.ai       → FAL_KEY no .env          (FLUX.1-schnell / dev)
  3. Stability AI → STABILITY_API_KEY no .env (SDXL)

Uso standalone:
  python3 -m agent.image_agent "campanha de verão para Source OF, estilo vibrante"
"""

import base64
import io
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

GEMINI_KEY       = os.getenv("GEMINI_API_KEY", "")
FAL_KEY          = os.getenv("FAL_KEY", "")
STABILITY_KEY    = os.getenv("STABILITY_API_KEY", "")
ANTHROPIC_KEY    = os.getenv("ANTHROPIC_API_KEY", "")

# Modelos Gemini para geração de imagem (em ordem de preferência)
GEMINI_IMAGE_MODELS = [
    os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
]

# Modelo fal.ai padrão
FAL_MODEL_DEFAULT = os.getenv("FAL_MODEL", "fal-ai/flux/schnell")
STABILITY_ENGINE  = "stable-diffusion-xl-1024-v1-0"

IMAGE_WIDTH  = int(os.getenv("IMAGE_WIDTH",  "1024"))
IMAGE_HEIGHT = int(os.getenv("IMAGE_HEIGHT", "1024"))
IMAGE_STEPS  = int(os.getenv("IMAGE_STEPS",  "4"))


# ── Prompt refinement via Claude ──────────────────────────────────────────────

REFINE_SYSTEM = """Você é um especialista em engenharia de prompts para geração de imagens com IA.

Sua tarefa: receber uma descrição em português (ou inglês) de uma imagem desejada
e retornar APENAS o prompt em inglês, otimizado para modelos de geração de imagem
(FLUX, Stable Diffusion, DALL-E).

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


# ── Providers ─────────────────────────────────────────────────────────────────


def _gerar_gemini(prompt_en: str) -> bytes:
    """
    Gera imagem via Gemini (gemini-2.5-flash-image ou superior).
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

def _gerar_fal(prompt_en: str, width: int, height: int, steps: int) -> bytes:
    """Gera imagem via fal.ai (FLUX). Retorna bytes PNG."""
    import fal_client

    os.environ["FAL_KEY"] = FAL_KEY

    model = FAL_MODEL_DEFAULT
    logger.info("[image] fal.ai → modelo=%s steps=%d", model, steps)

    result = fal_client.run(
        model,
        arguments={
            "prompt":           prompt_en,
            "image_size":       {"width": width, "height": height},
            "num_inference_steps": steps,
            "num_images":       1,
            "enable_safety_checker": False,
        },
    )

    images = result.get("images") or []
    if not images:
        raise ValueError("fal.ai não retornou imagens")

    img_info = images[0]
    img_url  = img_info.get("url", "")

    # Download da imagem
    import urllib.request
    with urllib.request.urlopen(img_url, timeout=30) as resp:
        return resp.read()


def _gerar_stability(prompt_en: str, width: int, height: int) -> bytes:
    """Gera imagem via Stability AI REST API v1. Retorna bytes PNG."""
    import requests

    url = f"https://api.stability.ai/v1/generation/{STABILITY_ENGINE}/text-to-image"
    headers = {
        "Authorization": f"Bearer {STABILITY_KEY}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }
    body = {
        "text_prompts": [{"text": prompt_en, "weight": 1.0}],
        "cfg_scale":    7,
        "height":       height,
        "width":        width,
        "samples":      1,
        "steps":        30,
    }
    resp = requests.post(url, headers=headers, json=body, timeout=60)
    if resp.status_code != 200:
        raise ValueError(f"Stability AI erro {resp.status_code}: {resp.text[:200]}")

    data   = resp.json()
    img_b64 = data["artifacts"][0]["base64"]
    return base64.b64decode(img_b64)


# ── Interface pública ─────────────────────────────────────────────────────────

class ImagemGerada:
    """Resultado de uma geração de imagem."""
    def __init__(
        self,
        bytes_png:      bytes,
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
    prompt: str,
    width:  int = IMAGE_WIDTH,
    height: int = IMAGE_HEIGHT,
    steps:  int = IMAGE_STEPS,
    refinar: bool = True,
) -> ImagemGerada:
    """
    Interface principal: recebe prompt PT/EN, refina com Claude,
    gera imagem e retorna ImagemGerada.

    Tenta fal.ai primeiro; cai para Stability em caso de falha.
    Levanta RuntimeError se todos os providers falharem.
    """
    prompt_original = prompt.strip()
    prompt_en = _refinar_prompt(prompt_original) if refinar else prompt_original

    # Slug seguro para nome de arquivo
    slug = re.sub(r"[^a-z0-9]+", "_", prompt_en.lower())[:40].strip("_")

    errors = []

    # Provider 1: Gemini (já configurado, provider padrão)
    if GEMINI_KEY:
        try:
            img_bytes = _gerar_gemini(prompt_en)
            return ImagemGerada(
                bytes_png=img_bytes,
                prompt_original=prompt_original,
                prompt_refinado=prompt_en,
                provider="Gemini (gemini-2.5-flash-image)",
                filename=f"imagem_{slug}.png",
            )
        except Exception as e:
            logger.warning("[image] Gemini falhou: %s", e)
            errors.append(f"Gemini: {e}")

    # Provider 2: fal.ai
    if FAL_KEY:
        try:
            img_bytes = _gerar_fal(prompt_en, width, height, steps)
            return ImagemGerada(
                bytes_png=img_bytes,
                prompt_original=prompt_original,
                prompt_refinado=prompt_en,
                provider="fal.ai (FLUX)",
                filename=f"imagem_{slug}.png",
            )
        except Exception as e:
            logger.warning("[image] fal.ai falhou: %s", e)
            errors.append(f"fal.ai: {e}")

    # Provider 3: Stability AI
    if STABILITY_KEY:
        try:
            img_bytes = _gerar_stability(prompt_en, width, height)
            return ImagemGerada(
                bytes_png=img_bytes,
                prompt_original=prompt_original,
                prompt_refinado=prompt_en,
                provider="Stability AI (SDXL)",
                filename=f"imagem_{slug}.png",
            )
        except Exception as e:
            logger.warning("[image] Stability falhou: %s", e)
            errors.append(f"Stability: {e}")

    if not any([GEMINI_KEY, FAL_KEY, STABILITY_KEY]):
        raise RuntimeError(
            "Nenhum provider de imagem configurado.\n"
            "Configure GEMINI_API_KEY (já disponível), FAL_KEY ou STABILITY_API_KEY no .env"
        )

    raise RuntimeError(
        "Todos os providers falharam:\n" + "\n".join(errors)
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
    print(f"   Provider: {result.provider}")
    print(f"   Prompt refinado: {result.prompt_refinado[:100]}")
