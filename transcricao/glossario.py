"""
transcricao/glossario.py — Grafia correta dos clientes nas transcrições.

O reconhecimento de voz do Meet ouve as marcas como palavras ("Sorso Soft", "Sorosof", "FEC").
normalizar() corrige o texto na ingestão (sync_reunioes.parsear_reuniao) e na saída do analisador;
`python glossario.py --aplicar` corrige o que já está no banco.

Para incluir um nome: (regex que casa as grafias erradas, grafia certa) em GLOSSARIO.
ponytail: lista curta de regex; se passar de ~30 nomes, mover para um JSON editável.
"""
from __future__ import annotations

import re
import sys

GLOSSARIO = [
    # Sorso Soft, Sorosof, SourceSoft, Sorsof, Sorsoft, Source Of, SourceOf — menos "source of truth"
    (re.compile(r"\bs[oa]u?r[cs]?[eo]?\s?-?\s?s?o(?:ft|f+)\b(?!\s+truth)", re.I), "Source Of"),
    (re.compile(r"\bFEC\b"), "FEQ"),
    (re.compile(r"\bPod\b"), "POD"),
    (re.compile(r"\bguara\s?media\b", re.I), "Guará Media"),
]

# Nomes oficiais, para o prompt do LLM
CLIENTES = "Source Of, POD Kombucha, FEQ, Botica Ouro Preto (agência: Guará Media)"


def normalizar(texto):
    if not isinstance(texto, str) or not texto:
        return texto
    for rx, certo in GLOSSARIO:
        # e-mails e URLs ficam como estão (pedro@guaramedia.com.br)
        texto = rx.sub(lambda m: m.group(0) if _em_endereco(m) else certo, texto)
    return texto


def _em_endereco(m) -> bool:
    s, a, b = m.string, m.start(), m.end()
    return (a > 0 and s[a - 1] in "@./") or (b + 1 < len(s) and s[b] in "@." and s[b + 1].isalnum())


CAMPOS = {
    "reunioes": ("titulo", "resumo", "decisoes", "proximas_etapas", "detalhes"),
    "analise_reunioes": ("titulo", "alertas", "clientes_mencionados", "resumo_analise"),
}


def corrigir_banco(conn, aplicar: bool) -> int:
    """Reescreve os campos de texto já gravados. Devolve quantas linhas mudam."""
    n = 0
    for tab, cols in CAMPOS.items():
        for row in conn.execute(f"SELECT id, {', '.join(cols)} FROM {tab}").fetchall():
            novo = [normalizar(v) for v in row[1:]]
            if novo != list(row[1:]):
                n += 1
                if aplicar:
                    conn.execute(f"UPDATE {tab} SET {', '.join(c + '=?' for c in cols)} WHERE id=?", (*novo, row[0]))
    if aplicar:
        conn.commit()
    return n


if __name__ == "__main__":
    casos = {
        "Refeito vídeos da Sorso Soft e da Pod": "Refeito vídeos da Source Of e da POD",
        "Sorosof, SourceSoft, Sorsof, Sorsoft, SourceOf": "Source Of, Source Of, Source Of, Source Of, Source Of",
        "Reunião FEC presencial; fechamento da FEC": "Reunião FEQ presencial; fechamento da FEQ",
        "single source of truth; o pode ficar": "single source of truth; o pode ficar",
        "time Guaramedia, pedro.rodrigues@guaramedia.com.br": "time Guará Media, pedro.rodrigues@guaramedia.com.br",
    }
    for entrada, esperado in casos.items():
        assert normalizar(entrada) == esperado, (entrada, normalizar(entrada))
    print("ok")
    if len(sys.argv) > 1:
        import sqlite3
        from pathlib import Path
        conn = sqlite3.connect(Path(__file__).parent / "db" / "reunioes.db")
        aplicar = "--aplicar" in sys.argv
        print(("corrigidas" if aplicar else "a corrigir (use --aplicar)"), corrigir_banco(conn, aplicar))
