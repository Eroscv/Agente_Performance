"""
agent/image_agent.py

Agente de Geração de Imagens do Agente de Performance Guará.

Fluxo:
  1. Recebe prompt do usuário (PT ou EN)
  2. Detecta proporção/formato pedido (banner, story, quadrado…)
  3. Claude refina e traduz o prompt para EN otimizado (com dados reais do cliente)
  4. Rate limit por usuário (máx 10 imagens/hora)
  5. Chama Gemini para gerar a imagem
  6. Loga no SQLite (image_log)
  7. Retorna ImagemGerada para o responder.py enviar no Slack

Provider: Gemini → GEMINI_API_KEY no .env (já configurado)
Modelos (em ordem): gemini-2.5-flash-image → gemini-3.1-flash-image → gemini-3-pro-image

Uso standalone:
  python3 -m agent.image_agent "banner horizontal Source OF, Black Friday"

Recebimento de imagem (edição/análise):
  editar_imagem(img_bytes, instrucao, user_id, channel, texto_slack)
  analisar_imagem(img_bytes, pergunta)
"""

import base64
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

GEMINI_KEY    = os.getenv("GEMINI_API_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

GEMINI_IMAGE_MODELS = [
    os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
]

_DB_PATH = Path(__file__).resolve().parent.parent / "agente.db"

# Rate limit: máx N gerações por usuário por janela de tempo
RATE_LIMIT_MAX    = int(os.getenv("IMAGE_RATE_LIMIT_MAX", "10"))
RATE_LIMIT_WINDOW = int(os.getenv("IMAGE_RATE_LIMIT_WINDOW_MIN", "60"))  # minutos


# ── Proporções / formatos ─────────────────────────────────────────────────────

# (largura, altura, label)
_FORMATOS = {
    # horizontal / banner
    "banner":      (1344, 768,  "banner 16:9"),
    "horizontal":  (1344, 768,  "banner 16:9"),
    "landscape":   (1344, 768,  "banner 16:9"),
    "16:9":        (1344, 768,  "banner 16:9"),
    "youtube":     (1344, 768,  "banner 16:9"),
    "capa":        (1344, 768,  "banner 16:9"),
    # vertical / story
    "story":       (768,  1344, "story 9:16"),
    "stories":     (768,  1344, "story 9:16"),
    "reels":       (768,  1344, "story 9:16"),
    "vertical":    (768,  1344, "story 9:16"),
    "9:16":        (768,  1344, "story 9:16"),
    "tiktok":      (768,  1344, "story 9:16"),
    # quadrado (padrão)
    "quadrado":    (1024, 1024, "quadrado 1:1"),
    "square":      (1024, 1024, "quadrado 1:1"),
    "post":        (1024, 1024, "quadrado 1:1"),
    "feed":        (1024, 1024, "quadrado 1:1"),
    "1:1":         (1024, 1024, "quadrado 1:1"),
    # largo / thumbnail
    "thumbnail":   (1344, 768,  "thumbnail 16:9"),
    "wide":        (1344, 768,  "thumbnail 16:9"),
}

_FORMATO_DEFAULT = (1024, 1024, "quadrado 1:1")

_FORMATO_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _FORMATOS) + r")\b",
    re.IGNORECASE,
)


def detectar_formato(text: str) -> tuple[int, int, str]:
    """Detecta proporção pedida no texto. Retorna (width, height, label)."""
    m = _FORMATO_RE.search(text)
    if m:
        return _FORMATOS[m.group(1).lower()]
    return _FORMATO_DEFAULT


# ── Detecção de intenção de imagem ────────────────────────────────────────────

_IMAGEM_RE = re.compile(
    # Verbos de criação seguidos de tipo visual
    r"\b(ger(?:a|e|ar?|ou|ando)|cr(?:ia|ie|iar?|iou|iando)|fa(?:z|ça|zer?|ça)|"
    r"draw|creat\w*|generat\w*|produz\w*|design\w*|mont\w*)"
    r".{0,40}\b(imagem|image|foto|ilustra\w*|banner|criativo|arte|design|visual|thumbnail|story|stories|reels|capa)\b"
    # Ou tipo visual primeiro, verbo depois
    r"|\b(imagem|banner|criativo|arte|visual|thumbnail|story|reels)\b.{0,40}"
    r"\b(ger(?:a|e|ar?)|cr(?:ia|ie|iar?)|fa(?:z|ça)|do|make|creat\w*|generat\w*)\b",
    re.IGNORECASE | re.DOTALL,
)

# Padrões que NÃO são pedido de geração mesmo contendo palavras visuais
_NAO_IMAGEM_RE = re.compile(
    r"\b(analisa?r?|analis[ae]|interpreta?r?|descrev[ea]r?|identifica?r?|"
    r"reconhec[ea]r?|extraia?r?|leia?r?\s+imagem|o\s+que\s+(h[aá]|tem|vejo|aparece))\b",
    re.IGNORECASE,
)


def e_pedido_imagem(text: str) -> bool:
    """
    Detecta pedido de geração de imagem.
    Evita falso positivo em 'analise esta imagem', 'o que há nessa foto', etc.
    Inclui pedidos de variação (refazer, outra versão).
    """
    if _NAO_IMAGEM_RE.search(text):
        return False
    return bool(_IMAGEM_RE.search(text)) or e_pedido_variacao(text)


def e_pedido_variacao(text: str) -> bool:
    """Detecta pedido de variação de imagem anterior ('refaça', 'outra versão', etc.)."""
    return bool(re.search(
        r"\b(refaz\w*|outra?\s+vers[aã]o|varia[çc]\w*|mesm[ao]\s+(tema|estilo|ideia)|"
        r"mas\s+com|agora\s+com|troca\s+[ao]|muda\s+[ao]|diferente\s+mas|"
        r"dessa\s+vez|tenta\s+de\s+novo|tenta\s+outra)\b",
        text, re.IGNORECASE,
    ))


# ── Rate limit ────────────────────────────────────────────────────────────────

def verificar_rate_limit(user_id: str) -> tuple[bool, int]:
    """
    Verifica se usuário está dentro do rate limit.
    Retorna (pode_gerar, contagem_atual).
    """
    if not user_id:
        return True, 0
    try:
        conn = sqlite3.connect(_DB_PATH)
        janela_inicio = (
            datetime.now(timezone.utc) - timedelta(minutes=RATE_LIMIT_WINDOW)
        ).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) FROM image_log WHERE user_id=? AND criado_em>=? AND status='ok'",
            (user_id, janela_inicio),
        ).fetchone()
        conn.close()
        contagem = row[0] if row else 0
        return contagem < RATE_LIMIT_MAX, contagem
    except Exception as e:
        logger.warning("[image] rate_limit check falhou: %s", e)
        return True, 0


# ── Log de geração ────────────────────────────────────────────────────────────

def _registrar_log(
    user_id: str,
    channel: str,
    prompt_original: str,
    prompt_refinado: str,
    modelo: str,
    size_kb: int,
    status: str = "ok",
    erro: str = "",
) -> None:
    try:
        conn = sqlite3.connect(_DB_PATH)
        conn.execute(
            """INSERT INTO image_log
               (user_id, channel, prompt_original, prompt_refinado, modelo, size_kb, status, erro, criado_em)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                user_id, channel, prompt_original, prompt_refinado,
                modelo, size_kb, status, erro,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("[image] falha ao registrar log: %s", e)


# ── Dados de cliente para prompt ──────────────────────────────────────────────

_CLIENTE_ESTILOS = {
    "source_of": (
        "Source OF — marca de beleza feminina brasileira. "
        "Estilo: clean, elegante, tons pastel (rosé, nude, branco), "
        "iluminação suave, pele radiante, produtos de skincare/beleza."
    ),
    "pod_kombucha": (
        "POD Kombucha — bebida fermentada natural, lifestyle saudável. "
        "Estilo: orgânico, fresco, cores vibrantes (verde, amarelo, laranja), "
        "natureza, bem-estar, pessoas ativas."
    ),
    "botica": (
        "Botica Ouro Preto — farmácia artesanal, cosmética natural. "
        "Estilo: sofisticado, tons terrosos (verde musgo, marrom, dourado), "
        "ingredientes naturais, embalagens artesanais, minimalista."
    ),
}

_CLIENTE_RE = re.compile(
    r"\b(source\s*of|source_of|pod\s*kombucha|pod_kombucha|pod\b|botica\b|botica\s+ouro\s+preto)\b",
    re.IGNORECASE,
)


def _detectar_estilo_cliente(texto: str) -> str:
    m = _CLIENTE_RE.search(texto)
    if not m:
        return ""
    nome = m.group(1).lower().replace(" ", "_")
    if "source" in nome:
        return _CLIENTE_ESTILOS["source_of"]
    if "pod" in nome:
        return _CLIENTE_ESTILOS["pod_kombucha"]
    if "botica" in nome:
        return _CLIENTE_ESTILOS["botica"]
    return ""


# ── Refinamento de prompt via Claude ─────────────────────────────────────────

_REFINE_SYSTEM_BASE = """Você é um especialista em engenharia de prompts para geração de imagens com IA.

Sua tarefa: receber uma descrição em português (ou inglês) e retornar APENAS o prompt
em inglês, otimizado para modelos de geração de imagem (Gemini Imagen).

Regras:
- Retorne SOMENTE o prompt em inglês, sem explicações, sem markdown, sem aspas
- Seja descritivo: estilo, iluminação, composição, qualidade, cores
- Máximo 200 palavras
- Contexto: marketing digital, criativos para Meta Ads / Google Ads
- Alta qualidade fotográfica ou ilustrativa conforme pedido
- Não inclua texto/letras na imagem a menos que explicitamente pedido
{estilo_cliente}"""


def _refinar_prompt(prompt_usuario: str, texto_original: str = "") -> str:
    """Usa Claude Haiku para refinar e traduzir o prompt, com contexto do cliente."""
    if not ANTHROPIC_KEY:
        return prompt_usuario

    estilo = _detectar_estilo_cliente(texto_original or prompt_usuario)
    system = _REFINE_SYSTEM_BASE.format(
        estilo_cliente=(
            f"\nContexto do cliente:\n{estilo}" if estilo
            else "\nAdapte para agência de marketing digital brasileira."
        )
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=300,
            system=system,
            messages=[{"role": "user", "content": prompt_usuario}],
        )
        refined = msg.content[0].text.strip().strip('"').strip("'")
        logger.info("[image] Prompt refinado: %s", refined[:80])
        return refined
    except Exception as e:
        logger.warning("[image] Falha ao refinar: %s — usando original", e)
        return prompt_usuario


# ── Provider: Gemini ──────────────────────────────────────────────────────────

def _gerar_gemini(prompt_en: str, width: int = 1024, height: int = 1024) -> tuple[bytes, str]:
    """
    Gera imagem via Gemini.
    Retorna (bytes_png, modelo_usado).
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_KEY)
    last_err = None

    # Adiciona instrução de proporção ao prompt quando não é quadrado
    prompt_final = prompt_en
    if width != height:
        ratio = "landscape/horizontal" if width > height else "portrait/vertical"
        prompt_final = f"{prompt_en}, {ratio} composition, {width}x{height} aspect ratio"

    for model_name in GEMINI_IMAGE_MODELS:
        try:
            logger.info("[image] Gemini → modelo=%s size=%dx%d", model_name, width, height)
            response = client.models.generate_content(
                model=model_name,
                contents=f"Generate an image: {prompt_final}",
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"]
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    raw = part.inline_data.data
                    img_bytes = bytes(raw) if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)
                    return img_bytes, model_name
            raise ValueError(f"{model_name} não retornou imagem")
        except Exception as e:
            logger.warning("[image] %s falhou: %s", model_name, str(e)[:120])
            last_err = e

    raise RuntimeError(f"Todos os modelos Gemini falharam. Último erro: {last_err}")


# ── Interface pública ─────────────────────────────────────────────────────────

class ImagemGerada:
    def __init__(
        self,
        bytes_png:       bytes,
        prompt_original: str,
        prompt_refinado: str,
        modelo:          str,
        filename:        str,
        formato_label:   str,
        width:           int,
        height:          int,
    ):
        self.bytes_png       = bytes_png
        self.prompt_original = prompt_original
        self.prompt_refinado = prompt_refinado
        self.provider        = "Gemini"
        self.modelo          = modelo
        self.filename        = filename
        self.formato_label   = formato_label
        self.width           = width
        self.height          = height
        self.size_kb         = len(bytes_png) // 1024


def gerar_imagem(
    prompt:       str,
    user_id:      str = "",
    channel:      str = "",
    texto_slack:  str = "",   # mensagem original do Slack (para detectar cliente/formato)
    refinar:      bool = True,
) -> ImagemGerada:
    """
    Interface principal.
    - Detecta formato/proporção no texto original
    - Refina prompt com contexto do cliente
    - Verifica rate limit
    - Gera via Gemini
    - Loga no SQLite
    """
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY não configurada no .env")

    texto_ref     = texto_slack or prompt
    width, height, fmt_label = detectar_formato(texto_ref)
    prompt_original = prompt.strip()
    prompt_en = _refinar_prompt(prompt_original, texto_ref) if refinar else prompt_original

    # Nome de arquivo com data e cliente detectado
    from agent.tz import hoje_brt
    hoje_str  = hoje_brt().strftime("%Y-%m-%d")
    hora_str  = datetime.now(timezone(timedelta(hours=-3))).strftime("%H-%M")
    cliente_m = _CLIENTE_RE.search(texto_ref)
    cliente_slug = ""
    if cliente_m:
        nome = cliente_m.group(1).lower().replace(" ", "_")
        if "source" in nome:   cliente_slug = "source_of_"
        elif "pod" in nome:    cliente_slug = "pod_"
        elif "botica" in nome: cliente_slug = "botica_"
    tema_slug = re.sub(r"[^a-z0-9]+", "_", prompt_en.lower())[:30].strip("_")
    filename  = f"{hoje_str}_{hora_str}_{cliente_slug}{tema_slug}.png"

    try:
        img_bytes, modelo_usado = _gerar_gemini(prompt_en, width, height)
    except Exception as e:
        _registrar_log(user_id, channel, prompt_original, prompt_en, "", 0, "erro", str(e))
        raise

    _registrar_log(user_id, channel, prompt_original, prompt_en,
                   modelo_usado, len(img_bytes) // 1024)

    return ImagemGerada(
        bytes_png=img_bytes,
        prompt_original=prompt_original,
        prompt_refinado=prompt_en,
        modelo=modelo_usado,
        filename=filename,
        formato_label=fmt_label,
        width=width,
        height=height,
    )

# ── Recebimento: edição e análise de imagem recebida ──────────────────────────

# Detecta se o usuário quer analisar/descrever (sem gerar nova imagem)
_ANALISAR_RE = re.compile(
    r"\b(analisa?r?|analis[ae]|descrev[ea]r?|descri[cç]|interpreta?r?|identifica?r?|avalia?r?|avali[ae]"
    r"|o\s+que|me\s+diz|me\s+fala|me\s+conta"
    r"|explica?r?|resume?|avalia?r?|critica?r?|feedback"
    r"|quais?\s+(cores?|elementos?|texto|produto|marca|estilo))\b",
    re.IGNORECASE,
)

# Detecta pedido de edição/alteração
_EDITAR_RE = re.compile(
    r"\b(muda?r?|altera?r?|troca?r?|coloca?r?|adiciona?r?|remov[ea]r?|apaga?r?"
    r"|deixa?r?|torna?r?|faça?|refaz|recria?r?|gera?r?|cria?r?|transforma?r?"
    r"|converte?r?|ajusta?r?|melhora?r?|otimiza?r?)\b",
    re.IGNORECASE,
)


def e_pedido_edicao_imagem(text: str) -> bool:
    """Detecta pedido de edição com imagem anexada (tem verbo de ação)."""
    return bool(_EDITAR_RE.search(text))


def e_pedido_analise_imagem(text: str) -> bool:
    """Detecta pedido de análise/descrição de imagem."""
    return bool(_ANALISAR_RE.search(text))


def analisar_imagem(img_bytes: bytes, pergunta: str = "", mime_type: str = "image/png") -> str:
    """
    Usa Gemini vision para analisar/descrever uma imagem recebida.
    Retorna texto em português.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_KEY)
    instrucao = pergunta.strip() if pergunta.strip() else (
        "Descreva esta imagem em detalhes: elementos visuais, cores, estilo, "
        "texto presente, produto ou marca identificada, e sua opinião sobre "
        "a qualidade como criativo de marketing digital. Responda em português."
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type=mime_type),
            instrucao,
        ],
    )
    return response.text.strip()


def editar_imagem(
    img_bytes:   bytes,
    instrucao:   str,
    user_id:     str = "",
    channel:     str = "",
    texto_slack: str = "",
    mime_type:   str = "image/png",
) -> "ImagemGerada":
    """
    Recebe imagem + instrução, envia ao Gemini vision+gen para reinterpretar
    com as modificações pedidas. Retorna ImagemGerada.
    Nota: Gemini reinterpreta (não faz inpainting pixel a pixel).
    """
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY não configurada")

    from google import genai
    from google.genai import types

    texto_ref = texto_slack or instrucao
    width, height, fmt_label = detectar_formato(texto_ref)
    instrucao_en = _refinar_prompt(instrucao, texto_ref) if ANTHROPIC_KEY else instrucao

    proporcao = ""
    if width != height:
        proporcao = f", {'landscape' if width > height else 'portrait'} orientation {width}x{height}"

    prompt_edicao = (
        f"Based on this image, create a new version with the following changes: "
        f"{instrucao_en}{proporcao}. "
        f"Keep the overall style, brand, and composition similar unless the change requires otherwise. "
        f"High quality, professional marketing creative."
    )

    client_g  = genai.Client(api_key=GEMINI_KEY)
    last_err  = None

    for model_name in GEMINI_IMAGE_MODELS:
        try:
            logger.info("[image:edit] modelo=%s", model_name)
            response = client_g.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type=mime_type),
                    prompt_edicao,
                ],
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"]
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    raw     = part.inline_data.data
                    img_out = bytes(raw) if isinstance(raw, (bytes, bytearray)) else base64.b64decode(raw)

                    from agent.tz import hoje_brt
                    hoje_str = hoje_brt().strftime("%Y-%m-%d")
                    hora_str = datetime.now(timezone(timedelta(hours=-3))).strftime("%H-%M")
                    cliente_m    = _CLIENTE_RE.search(texto_ref)
                    cliente_slug = ""
                    if cliente_m:
                        nome = cliente_m.group(1).lower().replace(" ", "_")
                        if "source" in nome:   cliente_slug = "source_of_"
                        elif "pod" in nome:    cliente_slug = "pod_"
                        elif "botica" in nome: cliente_slug = "botica_"
                    slug     = re.sub(r"[^a-z0-9]+", "_", instrucao_en.lower())[:25].strip("_")
                    filename = f"{hoje_str}_{hora_str}_{cliente_slug}edit_{slug}.png"

                    _registrar_log(user_id, channel,
                                   f"[EDIT] {instrucao}", instrucao_en,
                                   model_name, len(img_out) // 1024)
                    return ImagemGerada(
                        bytes_png=img_out,
                        prompt_original=instrucao,
                        prompt_refinado=instrucao_en,
                        modelo=model_name,
                        filename=filename,
                        formato_label=fmt_label,
                        width=width,
                        height=height,
                    )
            raise ValueError(f"{model_name} não retornou imagem")
        except Exception as e:
            logger.warning("[image:edit] %s falhou: %s", model_name, str(e)[:100])
            last_err = e

    _registrar_log(user_id, channel, f"[EDIT] {instrucao}", instrucao_en,
                   "", 0, "erro", str(last_err))
    raise RuntimeError(f"Gemini não conseguiu editar a imagem: {last_err}")



# ── Standalone ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    prompt = " ".join(sys.argv[1:]) or "banner horizontal Source OF, campanha de verão, estilo clean"
    print(f"Gerando: {prompt!r}")

    result = gerar_imagem(prompt, texto_slack=prompt)
    out = Path(f"/tmp/{result.filename}")
    out.write_bytes(result.bytes_png)
    print(f"✅ {out} ({result.size_kb} KB) — {result.formato_label}")
    print(f"   Modelo: {result.modelo}")
    print(f"   Prompt: {result.prompt_refinado[:100]}")
