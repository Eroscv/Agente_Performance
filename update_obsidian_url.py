#!/usr/bin/env python3
"""
update_obsidian_url.py — Atualiza OBSIDIAN_URL nos .env quando o tunnel Cloudflare mudar.

Uso:
    python update_obsidian_url.py <nova_url>

Exemplo:
    python update_obsidian_url.py https://nova-url.trycloudflare.com
"""

import sys
import re
from pathlib import Path

ENVS = [
    Path("/home/ubuntu/.openclaw/workspace/agente-performance/.env"),
    Path("/home/ubuntu/.openclaw/workspace/Flora-Agente-repo/.env"),
]

def update_url(nova_url: str):
    nova_url = nova_url.rstrip("/")
    for env_path in ENVS:
        if not env_path.exists():
            print(f"⚠️  Não encontrado: {env_path}")
            continue
        conteudo = env_path.read_text()
        if "OBSIDIAN_URL=" in conteudo:
            novo = re.sub(r"OBSIDIAN_URL=.*", f"OBSIDIAN_URL={nova_url}", conteudo)
            env_path.write_text(novo)
            print(f"✅ {env_path.name}: OBSIDIAN_URL atualizada → {nova_url}")
        else:
            conteudo += f"\nOBSIDIAN_URL={nova_url}\n"
            env_path.write_text(conteudo)
            print(f"✅ {env_path.name}: OBSIDIAN_URL adicionada → {nova_url}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python update_obsidian_url.py <nova_url>")
        sys.exit(1)
    update_url(sys.argv[1])
    print("\nPróximo passo: reiniciar os serviços para carregar a nova URL.")
    print("  sudo systemctl restart agente-guara.service")
    print("  sudo systemctl restart flora-agente.service")
