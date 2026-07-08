"""
slack/formatter.py

Converte o markdown gerado pelo Claude em Slack Block Kit.

Transformações:
  # Título        → header block
  ## Subtítulo    → section com *negrito*
  | tabela |      → section com fields (Slack não tem tabela nativa — usa código)
  - item          → bullet list em mrkdwn
  **texto**       → *texto* (bold Slack)
  ✅ ⚠️ etc       → mantidos como estão
  Parágrafos      → section blocks

Exporta:
  markdown_to_blocks(text) → list[dict]  (Slack Block Kit)
  truncar_blocos(blocks)   → list[dict]  (limita a 50 blocos, max Slack)
"""

import re
from typing import Any


# ── Helpers de formatação ─────────────────────────────────────────────────────

def _md_to_slack(text: str) -> str:
    """Converte markdown GitHub → mrkdwn Slack."""
    # **negrito** → *negrito*
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # __itálico__ → _itálico_
    text = re.sub(r"__(.+?)__", r"_\1_", text)
    # `código` → mantém igual (Slack aceita backtick)
    # [link](url) → <url|link>
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"<\2|\1>", text)
    # Remove marcadores ## que sobram
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    return text.strip()


def _formatar_tabela(linhas: list[str]) -> dict:
    """
    Converte tabela markdown em bloco de código para o Slack.
    Slack não renderiza tabelas | col | — usamos ```bloco``` para manter alinhamento.
    """
    # Extrai headers e separador
    rows = []
    headers = []
    for linha in linhas:
        if re.match(r"^\s*\|[-:\s|]+\|\s*$", linha):
            continue  # linha separadora --- skip
        cells = [c.strip() for c in linha.strip().strip("|").split("|")]
        rows.append(cells)

    if not rows:
        return None

    headers = rows[0]
    data    = rows[1:]

    if not data:
        # Só header, sem dados
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": " | ".join(f"*{h}*" for h in headers)},
        }

    # Calcula largura de cada coluna
    col_widths = [len(h) for h in headers]
    for row in data:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                # Remove markdown do cell para medir
                clean = re.sub(r"\*|_|`", "", cell)
                col_widths[i] = max(col_widths[i], len(clean))

    def pad(text, width):
        clean = re.sub(r"\*|_|`", "", text)
        return text + " " * (width - len(clean))

    # Monta tabela em texto fixo
    sep    = "─" * (sum(col_widths) + len(col_widths) * 3 + 1)
    header_row = " │ ".join(pad(h, col_widths[i]) for i, h in enumerate(headers))
    linhas_fmt = [sep, header_row, sep]
    for row in data:
        cells_fmt = []
        for i, cell in enumerate(row):
            w = col_widths[i] if i < len(col_widths) else 10
            cells_fmt.append(pad(_md_to_slack(cell), w))
        linhas_fmt.append(" │ ".join(cells_fmt))
    linhas_fmt.append(sep)

    return {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "```\n" + "\n".join(linhas_fmt) + "\n```",
        },
    }


# ── Conversor principal ───────────────────────────────────────────────────────

def markdown_to_blocks(text: str) -> list[dict]:
    """
    Converte texto markdown do Claude em lista de Slack Block Kit blocks.
    """
    blocks: list[dict] = []
    linhas  = text.splitlines()
    i       = 0
    buffer_paragrafo: list[str] = []
    buffer_tabela:    list[str] = []
    buffer_lista:     list[str] = []
    em_tabela = False

    def flush_paragrafo():
        nonlocal buffer_paragrafo
        if buffer_paragrafo:
            conteudo = _md_to_slack("\n".join(buffer_paragrafo).strip())
            if conteudo:
                blocks.append({
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": conteudo},
                })
            buffer_paragrafo = []

    def flush_lista():
        nonlocal buffer_lista
        if buffer_lista:
            texto = "\n".join(f"• {_md_to_slack(item)}" for item in buffer_lista)
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": texto},
            })
            buffer_lista = []

    def flush_tabela():
        nonlocal buffer_tabela, em_tabela
        if buffer_tabela:
            bloco = _formatar_tabela(buffer_tabela)
            if bloco:
                blocks.append(bloco)
            buffer_tabela = []
            em_tabela = False

    while i < len(linhas):
        linha = linhas[i]

        # ── Linha em branco ──
        if not linha.strip():
            flush_tabela()
            flush_lista()
            flush_paragrafo()
            i += 1
            continue

        # ── Tabela ──
        if re.match(r"^\s*\|", linha):
            flush_paragrafo()
            flush_lista()
            em_tabela = True
            buffer_tabela.append(linha)
            i += 1
            continue
        elif em_tabela:
            flush_tabela()

        # ── Cabeçalho H1 ──
        if re.match(r"^#\s+", linha):
            flush_paragrafo()
            flush_lista()
            texto = re.sub(r"^#\s+", "", linha).strip()
            # Remove emojis de título para o header (Slack os aceita)
            blocks.append({
                "type": "header",
                "text": {"type": "plain_text", "text": texto, "emoji": True},
            })
            blocks.append({"type": "divider"})
            i += 1
            continue

        # ── Cabeçalho H2/H3 ──
        if re.match(r"^#{2,3}\s+", linha):
            flush_paragrafo()
            flush_lista()
            texto = re.sub(r"^#{2,3}\s+", "", linha).strip()
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{_md_to_slack(texto)}*"},
            })
            i += 1
            continue

        # ── Blockquote (>) ──
        if re.match(r"^>\s+", linha):
            flush_paragrafo()
            flush_lista()
            texto = re.sub(r"^>\s+", "", linha).strip()
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f">{_md_to_slack(texto)}"},
            })
            i += 1
            continue

        # ── Lista com - ou * ou • ──
        if re.match(r"^[\-\*•]\s+", linha):
            flush_paragrafo()
            item = re.sub(r"^[\-\*•]\s+", "", linha).strip()
            buffer_lista.append(item)
            i += 1
            continue
        elif buffer_lista and linha.strip():
            # continuação de lista com indentação
            if re.match(r"^\s+", linha):
                buffer_lista[-1] += " " + linha.strip()
                i += 1
                continue
            else:
                flush_lista()

        # ── Divider (---) ──
        if re.match(r"^---+$", linha.strip()):
            flush_paragrafo()
            blocks.append({"type": "divider"})
            i += 1
            continue

        # ── Parágrafo normal ──
        buffer_paragrafo.append(linha)
        i += 1

    # Flush final
    flush_tabela()
    flush_lista()
    flush_paragrafo()

    return blocks or [{"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}]


def truncar_blocos(blocks: list[dict], max_blocks: int = 49) -> list[dict]:
    """
    Limita a lista de blocos ao máximo do Slack (50 blocos por mensagem).

    Se truncar, usa ordem de prioridade para descartar o menos importante:
      1. Resumo (header + callout KPI)          → nunca descartado
      2. Alertas                                → nunca descartado
      3. Top Campanhas / Comparativo períodos    → só descartados por último
      4. Tabela técnica detalhada               → descartada primeiro
      5. Alocação de budget                    → descartada antes da tabela principal
    """
    if len(blocks) <= max_blocks:
        return blocks

    # Detecta blocos por tipo de conteúdo para priorização
    def _is_low_priority(block: dict) -> bool:
        """Retorna True para blocos que podem ser descartados primeiro."""
        # Tabelas técnicas (toggle ou tabela com muitas colunas)
        if block.get("type") == "toggle":
            return True
        # Sections longas de alocação (bullet lists de budget)
        if block.get("type") == "bulleted_list_item":
            texto = ""
            for rt in block.get("bulleted_list_item", {}).get("rich_text", []):
                texto += rt.get("text", {}).get("content", "")
            # Budget allocation lines têm padrão: "R$ xxx (xx%)"
            if "%" in texto and "R$" in texto:
                return True
        return False

    # Separa em prioridade alta vs baixa
    high = [b for b in blocks if not _is_low_priority(b)]
    low  = [b for b in blocks if _is_low_priority(b)]

    # Tenta só com blocos de alta prioridade
    if len(high) <= max_blocks:
        result = high
        omitidos = len(blocks) - len(high)
    else:
        # Mesmo high priority precisa truncar — corta pelo final
        result   = high[:max_blocks - 1]
        omitidos = len(blocks) - len(result)

    result.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"_...{omitidos} bloco(s) de detalhes técnicos omitidos (limite Slack). Ver relatório completo no Notion._",
        },
    })
    return result
