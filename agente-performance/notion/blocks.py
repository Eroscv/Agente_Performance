"""
notion/blocks.py

Helpers de blocos Notion compartilhados por todos os builders.
Import: from notion.blocks import h2, paragrafo, divider, callout, tabela, bullet
"""
from __future__ import annotations


def h2(texto: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": texto}}]}}


def h3(texto: str) -> dict:
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": texto}}]}}


def paragrafo(texto: str, bold: bool = False) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": texto},
                                          "annotations": {"bold": bold}}]}}


def divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def callout(texto: str, emoji: str = "📊") -> dict:
    return {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": texto}}],
            "icon": {"type": "emoji", "emoji": emoji},
            "color": "gray_background",
        }
    }


def bullet(texto: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": texto}}]}}


def toggle(titulo: str, filhos: list[dict]) -> dict:
    return {
        "object": "block", "type": "toggle",
        "toggle": {
            "rich_text": [{"type": "text", "text": {"content": titulo}}],
            "children": filhos,
        }
    }


def codigo(texto: str, linguagem: str = "plain text") -> dict:
    return {
        "object": "block", "type": "code",
        "code": {
            "rich_text": [{"type": "text", "text": {"content": texto}}],
            "language": linguagem,
        }
    }


def tabela(header: list[str], linhas: list[list[str]]) -> dict:
    """Bloco tabela Notion com cabeçalho. Máx 100 linhas incluindo header."""
    def _cel(txt: str) -> list:
        return [{"type": "text", "text": {"content": str(txt)}}]

    rows = [{"type": "table_row", "table_row": {"cells": [_cel(h) for h in header]}}]
    for linha in linhas[:99]:  # 99 + 1 header = 100 (limite Notion)
        rows.append({"type": "table_row", "table_row": {"cells": [_cel(c) for c in linha]}})

    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(header),
            "has_column_header": True,
            "has_row_header": False,
            "children": rows,
        }
    }
