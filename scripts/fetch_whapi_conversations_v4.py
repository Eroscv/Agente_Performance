"""
fetch_whapi_conversations_v4.py

Puxa conversas e mensagens desde 01/07/2026 via Whapi.
- Paginação correta: usa `offset` (não `page`)
- Deduplicação de chats por ID
- Filtra chats com `timestamp >= 01/07/2026`
- Para a paginação quando o chat mais antigo for < 01/07/2026
- Limita a MAX_MSGS_PER_CHAT mensagens por conversa
"""

import json
import time
import httpx
from datetime import datetime, timezone

TOKEN       = "P7Z95nxhlvG6gyEavrXAeWic5RO4IgX2"
BASE        = "https://gate.whapi.cloud"
HEADERS     = {"Authorization": f"Bearer {TOKEN}"}
SINCE       = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
MAX_MSGS    = 500
BATCH       = 100
OUT_JSON    = "/home/ubuntu/.openclaw/workspace/scripts/conversas_julho2026.json"
OUT_TXT     = "/home/ubuntu/.openclaw/workspace/scripts/conversas_julho2026.txt"


def fmt(ts): return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def fetch_chats():
    seen = set()
    chats = []
    offset = 0
    total = None

    while True:
        r = httpx.get(f"{BASE}/chats?count={BATCH}&offset={offset}", headers=HEADERS, timeout=30)
        r.raise_for_status()
        d = r.json()
        if total is None:
            total = d.get("total", 0)

        batch = d.get("chats", [])
        if not batch:
            break

        oldest_ts = None
        for c in batch:
            cid = c.get("id", "")
            if cid in seen:
                continue
            seen.add(cid)
            ts = c.get("timestamp", 0)
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts
            if ts >= SINCE:
                chats.append(c)

        print(f"  offset={offset} | batch={len(batch)} | coletados={len(chats)} | "
              f"visto={len(seen)}/{total} | mais antigo={fmt(oldest_ts or 0)}")

        # Para quando os chats já são mais antigos que 01/07
        if oldest_ts and oldest_ts < SINCE:
            print("  → Chats mais antigos que 01/07 — parando.")
            break

        if len(seen) >= total:
            break

        offset += BATCH
        time.sleep(0.15)

    return chats


def fetch_msgs(chat_id):
    msgs = []
    offset = 0
    total = None

    while len(msgs) < MAX_MSGS:
        url = f"{BASE}/messages/list/{chat_id}?count={BATCH}&offset={offset}&after_time={SINCE}"
        try:
            r = httpx.get(url, headers=HEADERS, timeout=30)
            if r.status_code in (400, 404):
                break
            r.raise_for_status()
        except Exception as e:
            print(f"    ⚠ {chat_id}: {e}")
            break

        d = r.json()
        if total is None:
            total = d.get("total", 0)

        batch = d.get("messages", [])
        if not batch:
            break

        for m in batch:
            if m.get("timestamp", 0) >= SINCE:
                msgs.append(m)

        if total is not None and offset + BATCH >= total:
            break

        offset += BATCH
        time.sleep(0.1)

    return sorted(msgs[:MAX_MSGS], key=lambda m: m.get("timestamp", 0))


def content(msg):
    t = msg.get("type", "")
    if t == "text":
        return msg.get("text", {}).get("body", "")
    if t == "image":
        c = msg.get("image", {}).get("caption", "")
        return "[IMAGEM]" + (f" — {c}" if c else "")
    if t == "audio":
        return "[ÁUDIO]"
    if t == "video":
        c = msg.get("video", {}).get("caption", "")
        return "[VÍDEO]" + (f" — {c}" if c else "")
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
    print(f"\n🔍 Coletando conversas desde {fmt(SINCE)}\n")

    chats = fetch_chats()
    print(f"\n✅ {len(chats)} chats com atividade desde 01/07\n")

    result = []

    for i, chat in enumerate(chats):
        cid  = chat.get("id", "")
        if cid == "status@broadcast":
            continue

        name    = chat.get("name", "") or chat.get("last_message", {}).get("from_name", "")
        ctype   = chat.get("type", "contact")
        last_ts = chat.get("timestamp", 0)

        msgs     = fetch_msgs(cid)
        truncated = len(msgs) >= MAX_MSGS

        print(f"[{i+1}/{len(chats)}] {name or cid} → {len(msgs)} msgs"
              + (" ⚠TRUNC" if truncated else ""))

        result.append({
            "chat_id":     cid,
            "chat_type":   ctype,
            "name":        name,
            "last_ts":     last_ts,
            "msg_count":   len(msgs),
            "truncated":   truncated,
            "messages":    msgs,
        })

    # Salva JSON
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n💾 JSON: {OUT_JSON}")

    # Salva TXT
    total_msgs = sum(c["msg_count"] for c in result)
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(f"CONVERSAS WHAPI — DESDE 01/07/2026\n")
        f.write(f"Gerado: {fmt(int(datetime.now(timezone.utc).timestamp()))}\n")
        f.write(f"Total conversas: {len(result)} | Total mensagens: {total_msgs}\n")
        f.write("="*80 + "\n\n")

        for conv in result:
            trunc = " ⚠TRUNCADO" if conv.get("truncated") else ""
            f.write("="*80 + "\n")
            f.write(f"CHAT: {conv['name'] or conv['chat_id']}\n")
            f.write(f"ID: {conv['chat_id']} | Tipo: {conv['chat_type']}\n")
            f.write(f"Mensagens: {conv['msg_count']}{trunc}\n")
            f.write("-"*80 + "\n")

            for msg in conv["messages"]:
                direction = "→ BOT" if msg.get("from_me") else "← LEAD"
                sender    = (msg.get("from_name") or msg.get("from", "")
                             or ("Bot" if msg.get("from_me") else "Lead"))
                body      = content(msg)
                f.write(f"{fmt(msg.get('timestamp',0))} | {direction} | {sender}\n")
                if body:
                    f.write(f"  {body}\n")
                f.write("\n")

        f.write("="*80 + "\n")
        f.write(f"TOTAL: {len(result)} conversas | {total_msgs} mensagens\n")
        trunc_list = [c for c in result if c.get("truncated")]
        if trunc_list:
            f.write(f"TRUNCADOS ({MAX_MSGS} msgs max): {len(trunc_list)}\n")
            for t in trunc_list:
                f.write(f"  • {t['name'] or t['chat_id']}\n")

    print(f"📄 TXT: {OUT_TXT}")
    print(f"\n📊 RESUMO:")
    print(f"  Conversas: {len(result)}")
    print(f"  Mensagens: {total_msgs}")
    trunc_list = [c for c in result if c.get("truncated")]
    if trunc_list:
        print(f"  Truncados ({MAX_MSGS}): {len(trunc_list)}")
        for t in trunc_list:
            print(f"    • {t['name'] or t['chat_id']}")


if __name__ == "__main__":
    main()
