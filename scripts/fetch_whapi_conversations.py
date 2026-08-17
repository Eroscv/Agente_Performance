"""
fetch_whapi_conversations.py

Puxa todas as conversas e mensagens desde 01/07/2026 via API Whapi.
Salva o resultado em JSON + relatório legível em TXT.
"""

import json
import time
import httpx
from datetime import datetime, timezone

WHAPI_TOKEN = "P7Z95nxhlvG6gyEavrXAeWic5RO4IgX2"
WHAPI_BASE_URL = "https://gate.whapi.cloud"
HEADERS = {
    "Authorization": f"Bearer {WHAPI_TOKEN}",
    "Content-Type": "application/json",
}

# 01/07/2026 00:00:00 UTC em Unix timestamp
SINCE_TIMESTAMP = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())

OUTPUT_JSON = "/home/ubuntu/.openclaw/workspace/scripts/conversas_whapi_julio2026.json"
OUTPUT_TXT  = "/home/ubuntu/.openclaw/workspace/scripts/conversas_whapi_julio2026.txt"


def ts_to_str(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def fetch_chats() -> list[dict]:
    """Busca todos os chats disponíveis (com paginação)."""
    chats = []
    page = 0
    count = 100
    while True:
        url = f"{WHAPI_BASE_URL}/chats?count={count}&page={page}"
        resp = httpx.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("chats", [])
        if not batch:
            break
        chats.extend(batch)
        total = data.get("total", 0)
        print(f"  Chats buscados: {len(chats)} / {total}")
        if len(chats) >= total:
            break
        page += 1
    return chats


def fetch_messages_for_chat(chat_id: str, since: int) -> list[dict]:
    """Busca todas as mensagens de um chat que sejam >= since (timestamp Unix)."""
    messages = []
    page = 0
    count = 100
    oldest_seen = None

    while True:
        url = f"{WHAPI_BASE_URL}/messages/list/{chat_id}?count={count}&page={page}"
        try:
            resp = httpx.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            print(f"    ⚠ HTTP {e.response.status_code} para chat {chat_id}")
            break
        except Exception as e:
            print(f"    ⚠ Erro ao buscar msgs de {chat_id}: {e}")
            break

        data = resp.json()
        batch = data.get("messages", [])
        if not batch:
            break

        # A Whapi retorna mensagens em ordem decrescente (mais recente primeiro)
        for msg in batch:
            ts = msg.get("timestamp", 0)
            if ts >= since:
                messages.append(msg)
                oldest_seen = ts
            else:
                # Chegamos em msgs anteriores a 01/07 — podemos parar
                return messages

        total = data.get("total", 0)
        fetched_so_far = (page + 1) * count
        if fetched_so_far >= total:
            break

        page += 1
        time.sleep(0.2)  # rate limit gentil

    return messages


def main():
    print(f"\n🔍 Buscando conversas desde {ts_to_str(SINCE_TIMESTAMP)} ...\n")

    all_chats = fetch_chats()
    print(f"\n✅ Total de chats encontrados: {len(all_chats)}\n")

    result = []

    for chat in all_chats:
        chat_id   = chat.get("id", "")
        chat_type = chat.get("type", "")
        last_ts   = chat.get("timestamp", 0)
        name      = chat.get("name", "") or chat.get("last_message", {}).get("from_name", "")

        # Ignora stories / broadcasts se não tiver mensagens recentes
        if last_ts < SINCE_TIMESTAMP and chat_type in ("stories", "broadcast"):
            continue

        # Otimização: se a última mensagem do chat é anterior a 01/07, pula
        if last_ts < SINCE_TIMESTAMP:
            continue

        print(f"📱 [{chat_type}] {name or chat_id} (última: {ts_to_str(last_ts)})")

        messages = fetch_messages_for_chat(chat_id, SINCE_TIMESTAMP)
        print(f"   → {len(messages)} mensagem(ns) desde 01/07/2026")

        if messages:
            result.append({
                "chat_id":   chat_id,
                "chat_type": chat_type,
                "name":      name,
                "last_timestamp": last_ts,
                "messages_since_july": len(messages),
                "messages": sorted(messages, key=lambda m: m.get("timestamp", 0)),
            })

    # Salva JSON completo
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n💾 JSON salvo em: {OUTPUT_JSON}")

    # Gera relatório legível
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write(f"CONVERSAS WHAPI — DESDE 01/07/2026\n")
        f.write(f"Gerado em: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        f.write(f"{'='*80}\n\n")

        for conv in result:
            f.write(f"{'='*80}\n")
            f.write(f"CHAT: {conv['name'] or conv['chat_id']}\n")
            f.write(f"ID: {conv['chat_id']} | Tipo: {conv['chat_type']}\n")
            f.write(f"Mensagens no período: {conv['messages_since_july']}\n")
            f.write(f"{'─'*80}\n")

            for msg in conv["messages"]:
                ts_str     = ts_to_str(msg.get("timestamp", 0))
                from_me    = msg.get("from_me", False)
                direction  = "→ BOT" if from_me else "← LEAD"
                msg_type   = msg.get("type", "?")
                from_name  = msg.get("from_name", "")
                from_num   = msg.get("from", "")

                sender = from_name or from_num or ("Bot" if from_me else "Lead")

                # Extrai conteúdo conforme tipo
                content = ""
                if msg_type == "text":
                    content = msg.get("text", {}).get("body", "")
                elif msg_type == "image":
                    caption = msg.get("image", {}).get("caption", "")
                    content = f"[IMAGEM]{' — ' + caption if caption else ''}"
                elif msg_type == "audio":
                    content = "[ÁUDIO]"
                elif msg_type == "video":
                    caption = msg.get("video", {}).get("caption", "")
                    content = f"[VÍDEO]{' — ' + caption if caption else ''}"
                elif msg_type == "document":
                    fname = msg.get("document", {}).get("file_name", "arquivo")
                    content = f"[DOCUMENTO: {fname}]"
                elif msg_type == "interactive":
                    body = msg.get("interactive", {}).get("body", {}).get("text", "")
                    content = f"[INTERATIVO] {body}"
                elif msg_type == "button_reply":
                    title = msg.get("button_reply", {}).get("title", "")
                    content = f"[BOTÃO: {title}]"
                elif msg_type == "list_reply":
                    title = msg.get("list_reply", {}).get("title", "")
                    content = f"[LISTA: {title}]"
                elif msg_type == "story":
                    content = "[STORY]"
                else:
                    content = f"[{msg_type.upper()}]"

                f.write(f"{ts_str} | {direction} | {sender}\n")
                if content:
                    f.write(f"  {content}\n")
                f.write("\n")

        f.write(f"\n{'='*80}\n")
        f.write(f"TOTAL: {len(result)} conversa(s) com atividade desde 01/07/2026\n")
        total_msgs = sum(c["messages_since_july"] for c in result)
        f.write(f"TOTAL MENSAGENS: {total_msgs}\n")

    print(f"📄 Relatório TXT salvo em: {OUTPUT_TXT}")
    print(f"\n📊 RESUMO:")
    print(f"  Conversas ativas desde 01/07: {len(result)}")
    total_msgs = sum(c["messages_since_july"] for c in result)
    print(f"  Total de mensagens: {total_msgs}")
    for conv in result:
        print(f"  • {conv['name'] or conv['chat_id']} — {conv['messages_since_july']} msg(s)")


if __name__ == "__main__":
    main()
