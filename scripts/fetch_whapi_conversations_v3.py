"""
fetch_whapi_conversations_v3.py

Puxa todas as conversas e mensagens desde 01/07/2026 via API Whapi.
v3: deduplicação de chats + limitado a 200 msgs/chat.
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

SINCE_TIMESTAMP = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
MAX_MSGS_PER_CHAT = 500

OUTPUT_JSON = "/home/ubuntu/.openclaw/workspace/scripts/conversas_whapi_julho2026.json"
OUTPUT_TXT  = "/home/ubuntu/.openclaw/workspace/scripts/conversas_whapi_julho2026.txt"


def ts_to_str(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def fetch_chats() -> list[dict]:
    """Busca todos os chats únicos com atividade desde 01/07/2026."""
    seen_ids = set()
    chats = []
    page = 0
    count = 100

    while True:
        url = f"{WHAPI_BASE_URL}/chats?count={count}&page={page}"
        resp = httpx.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("chats", [])
        total = data.get("total", 0)

        if not batch:
            break

        new_in_batch = 0
        oldest_ts = None
        for chat in batch:
            cid = chat.get("id", "")
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            ts = chat.get("timestamp", 0)
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts
            if ts >= SINCE_TIMESTAMP:
                chats.append(chat)
            new_in_batch += 1

        print(f"  Página {page}: {new_in_batch} chats novos | total coletados: {len(chats)} | "
              f"visto: {len(seen_ids)}/{total} | mais antigo: {ts_to_str(oldest_ts or 0)}")

        # Para se já vimos mais que o total ou se chats são muito antigos
        if len(seen_ids) >= total:
            break
        if oldest_ts and oldest_ts < SINCE_TIMESTAMP:
            print("  (chats antigos demais — parando)")
            break

        page += 1
        time.sleep(0.1)

    return chats


def fetch_messages_for_chat(chat_id: str, since: int) -> list[dict]:
    """Busca mensagens de um chat desde `since` via after_time."""
    messages = []
    page = 0
    count = 100

    while len(messages) < MAX_MSGS_PER_CHAT:
        url = f"{WHAPI_BASE_URL}/messages/list/{chat_id}?count={count}&page={page}&after_time={since}"
        try:
            resp = httpx.get(url, headers=HEADERS, timeout=30)
            if resp.status_code in (400, 404):
                break
            resp.raise_for_status()
        except Exception as e:
            print(f"    ⚠ Erro {chat_id}: {e}")
            break

        data = resp.json()
        batch = data.get("messages", [])
        if not batch:
            break

        for msg in batch:
            if msg.get("timestamp", 0) >= since:
                messages.append(msg)

        total = data.get("total", 0)
        if (page + 1) * count >= total:
            break

        page += 1
        time.sleep(0.1)

    return sorted(messages[:MAX_MSGS_PER_CHAT], key=lambda m: m.get("timestamp", 0))


def msg_content(msg: dict) -> str:
    t = msg.get("type", "")
    if t == "text":
        return msg.get("text", {}).get("body", "")
    if t == "image":
        c = msg.get("image", {}).get("caption", "")
        return f"[IMAGEM]{' — ' + c if c else ''}"
    if t == "audio":
        return "[ÁUDIO]"
    if t == "video":
        c = msg.get("video", {}).get("caption", "")
        return f"[VÍDEO]{' — ' + c if c else ''}"
    if t == "document":
        fn = msg.get("document", {}).get("file_name", "arquivo")
        return f"[DOCUMENTO: {fn}]"
    if t == "interactive":
        b = msg.get("interactive", {}).get("body", {}).get("text", "")
        return f"[INTERATIVO] {b}"
    if t == "button_reply":
        return f"[BOTÃO: {msg.get('button_reply', {}).get('title', '')}]"
    if t == "list_reply":
        return f"[LISTA: {msg.get('list_reply', {}).get('title', '')}]"
    if t == "story":
        return "[STORY]"
    return f"[{t.upper()}]"


def main():
    print(f"\n🔍 Buscando conversas desde {ts_to_str(SINCE_TIMESTAMP)} ...\n")
    all_chats = fetch_chats()
    print(f"\n✅ {len(all_chats)} chats únicos com atividade desde 01/07/2026\n")

    result = []

    for i, chat in enumerate(all_chats):
        chat_id   = chat.get("id", "")
        chat_type = chat.get("type", "")
        last_ts   = chat.get("timestamp", 0)
        name      = chat.get("name", "") or chat.get("last_message", {}).get("from_name", "")

        if chat_id == "status@broadcast":
            continue

        messages = fetch_messages_for_chat(chat_id, SINCE_TIMESTAMP)
        truncated = len(messages) >= MAX_MSGS_PER_CHAT
        print(f"[{i+1}/{len(all_chats)}] {name or chat_id} → {len(messages)} msgs"
              + (" ⚠TRUNC" if truncated else ""))

        result.append({
            "chat_id":   chat_id,
            "chat_type": chat_type,
            "name":      name,
            "last_timestamp": last_ts,
            "messages_since_july": len(messages),
            "truncated": truncated,
            "messages":  messages,
        })

    # JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n💾 JSON → {OUTPUT_JSON}")

    # TXT
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write(f"CONVERSAS WHAPI — DESDE 01/07/2026\n")
        f.write(f"Gerado em: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        f.write("="*80 + "\n\n")

        for conv in result:
            f.write("="*80 + "\n")
            trunc = " ⚠ TRUNCADO" if conv.get("truncated") else ""
            f.write(f"CHAT: {conv['name'] or conv['chat_id']}\n")
            f.write(f"ID: {conv['chat_id']} | Tipo: {conv['chat_type']}\n")
            f.write(f"Mensagens: {conv['messages_since_july']}{trunc}\n")
            f.write("-"*80 + "\n")

            for msg in conv["messages"]:
                direction = "→ BOT" if msg.get("from_me") else "← LEAD"
                sender    = msg.get("from_name") or msg.get("from", "") or ("Bot" if msg.get("from_me") else "Lead")
                content   = msg_content(msg)
                f.write(f"{ts_to_str(msg.get('timestamp',0))} | {direction} | {sender}\n")
                if content:
                    f.write(f"  {content}\n")
                f.write("\n")

        total_msgs = sum(c["messages_since_july"] for c in result)
        truncated  = [c for c in result if c.get("truncated")]
        f.write("="*80 + "\n")
        f.write(f"TOTAL: {len(result)} conversa(s)\n")
        f.write(f"TOTAL MENSAGENS: {total_msgs}\n")
        if truncated:
            f.write(f"CHATS TRUNCADOS ({MAX_MSGS_PER_CHAT} msgs max): {len(truncated)}\n")
            for t in truncated:
                f.write(f"  • {t['name'] or t['chat_id']}\n")

    total_msgs = sum(c["messages_since_july"] for c in result)
    truncated  = [c for c in result if c.get("truncated")]
    print(f"📄 TXT → {OUTPUT_TXT}")
    print(f"\n📊 RESUMO FINAL:")
    print(f"  Conversas: {len(result)}")
    print(f"  Mensagens: {total_msgs}")
    if truncated:
        print(f"  Truncados ({MAX_MSGS_PER_CHAT}): {len(truncated)}")
        for t in truncated:
            print(f"    • {t['name'] or t['chat_id']}")


if __name__ == "__main__":
    main()
