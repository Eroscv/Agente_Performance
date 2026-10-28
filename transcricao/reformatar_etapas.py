"""
reformatar_etapas.py

Reprocessa o campo proximas_etapas de reuniões com formato livre,
convertendo para o padrão [Nome Sobrenome] Título: Descrição via Claude.

Uso:
  python3 reformatar_etapas.py           # dry-run (mostra o que mudaria)
  python3 reformatar_etapas.py --aplicar # aplica no banco
"""
import argparse
import logging
import re
import sqlite3
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / "agente-performance" / ".env")

from sync_reunioes import DB_PATH, reformatar_proximas_etapas

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_JA_FORMATADO = re.compile(r"\[[A-Z][a-z]+ [A-Z][a-z]+\]")


def precisa_reformatar(texto: str) -> bool:
    if not texto or not texto.strip():
        return False
    return not bool(_JA_FORMATADO.search(texto))


def main(aplicar: bool = False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT id, fonte_id, titulo, data, proximas_etapas
        FROM reunioes
        WHERE proximas_etapas IS NOT NULL AND proximas_etapas != ''
        ORDER BY data DESC
    """).fetchall()

    total = len(rows)
    precisa = [r for r in rows if precisa_reformatar(r["proximas_etapas"])]
    ja_ok   = total - len(precisa)

    logger.info("Total com etapas: %d | Já formatadas: %d | Precisam reformatar: %d",
                total, ja_ok, len(precisa))

    if not precisa:
        logger.info("Nada a fazer.")
        conn.close()
        return

    atualizadas = erros = 0

    for row in precisa:
        titulo  = row["titulo"]
        data    = row["data"]
        etapas  = row["proximas_etapas"]

        logger.info("  → %s (%s)", titulo, data)
        novo_texto = reformatar_proximas_etapas(etapas)

        if novo_texto == etapas:
            logger.warning("    ⚠️  Claude retornou texto igual — pulando")
            erros += 1
            continue

        # Mostra diff resumido
        linhas_novas = [l for l in novo_texto.splitlines() if l.strip()]
        logger.info("    ✅ %d tarefa(s) extraída(s):", len(linhas_novas))
        for linha in linhas_novas[:3]:
            logger.info("       %s", linha[:80])
        if len(linhas_novas) > 3:
            logger.info("       ... +%d", len(linhas_novas) - 3)

        if aplicar:
            conn.execute(
                "UPDATE reunioes SET proximas_etapas = ?, processado_comunicacao = 0 WHERE id = ?",
                (novo_texto, row["id"])
            )
            conn.commit()
            atualizadas += 1
        else:
            atualizadas += 1  # conta como "seria atualizado"

    conn.close()

    modo = "APLICADO" if aplicar else "DRY-RUN"
    logger.info("\n[%s] Atualizadas: %d | Erros: %d", modo, atualizadas, erros)
    if not aplicar:
        logger.info("Rode com --aplicar para salvar no banco.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--aplicar", action="store_true", help="Salva no banco (padrão: dry-run)")
    args = parser.parse_args()
    main(aplicar=args.aplicar)
