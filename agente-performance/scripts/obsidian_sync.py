"""
obsidian_sync.py — Integração com Obsidian Local REST API
Agente de Performance Guará

Funções:
- append_registro_alteracoes(): Adiciona entrada no log de alterações
- update_metricas(): Atualiza snapshot de métricas
- get_vault_file(): Lê arquivo do vault
- put_vault_file(): Escreve/sobrescreve arquivo no vault
"""

import os
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
OBSIDIAN_URL   = os.getenv("OBSIDIAN_URL", "").rstrip("/")
OBSIDIAN_TOKEN = os.getenv("OBSIDIAN_TOKEN", "")

BRT = timezone(timedelta(hours=-3))

# Paths no vault
PATH_REGISTRO   = "01 Projetos/Agente de Performance/Agente de Performance - Registro de Alterações.md"
PATH_METRICAS   = "01 Projetos/Agente de Performance/Agente de Performance - Métricas.md"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {OBSIDIAN_TOKEN}",
        "Content-Type": "text/markdown",
    }


def _enabled() -> bool:
    if not OBSIDIAN_URL or not OBSIDIAN_TOKEN:
        logger.debug("obsidian_sync: OBSIDIAN_URL ou OBSIDIAN_TOKEN não configurados — skip")
        return False
    return True


def _encode_path(path: str) -> str:
    """Codifica path para URL (preserva /)"""
    from urllib.parse import quote
    return quote(path, safe="/")


def get_vault_file(path: str) -> Optional[str]:
    """Lê conteúdo de um arquivo do vault. Retorna None se não encontrado."""
    if not _enabled():
        return None
    try:
        r = requests.get(
            f"{OBSIDIAN_URL}/vault/{_encode_path(path)}",
            headers={"Authorization": f"Bearer {OBSIDIAN_TOKEN}"},
            timeout=10,
        )
        if r.status_code == 200:
            return r.text
        logger.warning("obsidian_sync get %s: HTTP %s", path, r.status_code)
        return None
    except Exception as e:
        logger.error("obsidian_sync get_vault_file: %s", e)
        return None


def put_vault_file(path: str, content: str) -> bool:
    """Escreve/sobrescreve arquivo no vault."""
    if not _enabled():
        return False
    try:
        r = requests.put(
            f"{OBSIDIAN_URL}/vault/{_encode_path(path)}",
            headers=_headers(),
            data=content.encode("utf-8"),
            timeout=10,
        )
        ok = r.status_code in (200, 204)
        if not ok:
            logger.warning("obsidian_sync put %s: HTTP %s — %s", path, r.status_code, r.text[:200])
        return ok
    except Exception as e:
        logger.error("obsidian_sync put_vault_file: %s", e)
        return False


def append_registro_alteracoes(commit: str, descricao: str, data: Optional[str] = None) -> bool:
    """
    Adiciona linha na tabela de Registro de Alterações do Agente de Performance.

    Args:
        commit: Hash curto ou mensagem do commit (ex: "a1b2c3d" ou "feat: novo relatório")
        descricao: Descrição legível da alteração
        data: Data no formato YYYY-MM-DD (default: hoje BRT)
    """
    if not _enabled():
        return False

    data = data or datetime.now(BRT).strftime("%Y-%m-%d")
    nova_linha = f"| {data} | `{commit}` | {descricao} |"

    conteudo = get_vault_file(PATH_REGISTRO)
    if conteudo is None:
        logger.error("obsidian_sync: não foi possível ler %s", PATH_REGISTRO)
        return False

    # Insere após o cabeçalho da tabela (linha com |---|)
    linhas = conteudo.splitlines()
    idx_sep = None
    for i, linha in enumerate(linhas):
        if re.match(r"^\|[-| ]+\|$", linha.strip()):
            idx_sep = i
            break

    if idx_sep is None:
        logger.error("obsidian_sync: tabela não encontrada em %s", PATH_REGISTRO)
        return False

    linhas.insert(idx_sep + 1, nova_linha)
    novo_conteudo = "\n".join(linhas) + "\n"

    ok = put_vault_file(PATH_REGISTRO, novo_conteudo)
    if ok:
        logger.info("obsidian_sync: registro de alteração adicionado — %s", descricao)
    return ok


def update_metricas(dados: dict) -> bool:
    """
    Atualiza seção de métricas no vault.

    Args:
        dados: dict com chaves opcionais:
            - dias_sincronizados: int
            - total_pedidos: int
            - receita_total: str (ex: "R$ 9,85M")
            - ultima_sync: str (ex: "2026-09-02 06:00 BRT")
            - notas_extras: list[str] — linhas adicionais
    """
    if not _enabled():
        return False

    conteudo = get_vault_file(PATH_METRICAS)
    if conteudo is None:
        logger.error("obsidian_sync: não foi possível ler %s", PATH_METRICAS)
        return False

    agora = datetime.now(BRT).strftime("%Y-%m-%d %H:%M BRT")

    def _sub(pattern: str, replacement: str, text: str) -> str:
        return re.sub(pattern, replacement, text, flags=re.MULTILINE)

    if "dias_sincronizados" in dados:
        conteudo = _sub(
            r"(- \*\*Dias sincronizados:\*\* ).*",
            rf"\g<1>{dados['dias_sincronizados']}",
            conteudo,
        )
    if "total_pedidos" in dados:
        conteudo = _sub(
            r"(- \*\*Pedidos:\*\* ).*",
            rf"\g<1>{dados['total_pedidos']:,}".replace(",", "."),
            conteudo,
        )
    if "receita_total" in dados:
        conteudo = _sub(
            r"(- \*\*Receita total:\*\* ).*",
            rf"\g<1>{dados['receita_total']}",
            conteudo,
        )
    if "ultima_sync" in dados:
        conteudo = _sub(
            r"(- \*\*Última sync:\*\* ).*",
            rf"\g<1>{dados['ultima_sync']}",
            conteudo,
        )

    # Atualiza rodapé de última atualização
    if "Atualizado em:" in conteudo:
        conteudo = _sub(r"Atualizado em:.*", f"Atualizado em: {agora}", conteudo)
    else:
        conteudo = conteudo.rstrip() + f"\n\n> Atualizado em: {agora}\n"

    ok = put_vault_file(PATH_METRICAS, conteudo)
    if ok:
        logger.info("obsidian_sync: métricas atualizadas em %s", agora)
    return ok


# ── CLI rápido para teste ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "ping"

    if cmd == "ping":
        r = requests.get(
            f"{OBSIDIAN_URL}/",
            headers={"Authorization": f"Bearer {OBSIDIAN_TOKEN}"},
            timeout=10,
        )
        print(f"Status: {r.status_code}")
        print(r.json().get("status", r.text))

    elif cmd == "registro":
        commit = sys.argv[2] if len(sys.argv) > 2 else "teste"
        desc   = sys.argv[3] if len(sys.argv) > 3 else "Teste de integração Obsidian"
        ok = append_registro_alteracoes(commit, desc)
        print("OK" if ok else "FALHOU")

    elif cmd == "metricas":
        ok = update_metricas({
            "ultima_sync": datetime.now(BRT).strftime("%Y-%m-%d %H:%M BRT"),
        })
        print("OK" if ok else "FALHOU")

    else:
        print(f"Uso: python obsidian_sync.py [ping|registro|metricas]")
