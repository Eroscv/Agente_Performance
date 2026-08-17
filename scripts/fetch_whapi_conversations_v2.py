"""
fetch_whapi_conversations_v2.py

Puxa todas as conversas e mensagens desde 01/07/2026 via API Whapi.
Versão 2: usa parâmetro `after_time` da API para filtrar por data diretamente,
e limita a 500 msgs por chat para evitar timeouts.

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

# Máximo de mensagens por chat (para evitar timeout em chats com milhares de msgs)
MAX_MSGS_PER_CHAT = 500

OUTPUT_JSON = "/home/ubuntu/.openclaw/workspace/scripts/conversas_whapi_julio2026.json"
OUTPUT_TXT  = "/home/ubuntu/.openclaw/workspace/scripts/conversas_whapi_julio2026.txt"


def ts_to_str(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def fetch_chats() -> list[dict]:
    """Busca todos os chats disponíveis (com paginação)."""
    chats = []
    page = 0
    count = 100
    total = None

    while True:
        url = f"{WHAPI_BASE_URL}/chats?count={count}&page={page}"
        resp = httpx.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("chats", [])
        if not batch:
            break
        chats.extend(batch)
        if total is None:
            total = data.get("total", 0)
        print(f"  Chats buscados: {len(chats)} / {total}")

        # Para quando a última mensagem do lote for anterior a 01/07
        last_ts = batch[-1].get("timestamp", 0)
        if last_ts < SINCE_TIMESTAMP:
            print(f"  (últimos chats já são anteriores a 01/07 — parando paginação)")
            break

        if len(chats) >= total:
            break
        page += 1
        time.sleep(0.1)

    # Filtra apenas chats com atividade >= 01/07
    before = len(chats)
    chats = [c for c in chats if c.get("timestamp", 0) >= SINCE_TIMESTAMP]
    print(f"  → {len(chats)} chats com atividade desde 01/07 (de {before} total)")
    return chats


def fetch_messages_for_chat(chat_id: str, since: int) -> list[dict]:
    """
    Busca mensagens de um chat desde `since`.
    Usa after_time se disponível, senão pagina manualmente.
    Limita a MAX_MSGS_PER_CHAT mensagens.
    """
    messages = []
    page = 0
    count = 100

    while len(messages) < MAX_MSGS_PER_CHAT:
        # Tenta usar o parâmetro after_time (timestamp Unix) para filtragem na API
        url = f"{WHAPI_BASE_URL}/messages/list/{chat_id}?count={count}&page={page}&after_time={since}"
        try:
            resp = httpx.get(url, headers=HEADERS, timeout=30)
            if resp.status_code == 400:
                # after_time não suportado para este tipo de chat
                break
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

        # A API com after_time já retorna somente msgs >= since
        # Mas filtramos por garantia
        for msg in batch:
            ts = msg.get("timestamp", 0)
            if ts >= since:
                messages.append(msg)

        total = data.get("total", 0)
        fetched_so_far = (page + 1) * count
        if fetched_so_far >= total:
            break

        page += 1
        time.sleep(0.15)

    return messages[:MAX_MSGS_PER_CHAT]


def main():
    print(f"\n🔍 Buscando conversas desde {ts_to_str(SINCE_TIMESTAMP)} ...\n")

    all_chats = fetch_chats()
    print(f"\n✅ Processando {len(all_chats)} chats...\n")

    result = []

    for i, chat in enumerate(all_chats):
        chat_id   = chat.get("id", "")
        chat_type = chat.get("type", "")
        last_ts   = chat.get("timestamp", 0)
        name      = chat.get("name", "") or chat.get("last_message", {}).get("from_name", "")

        # Ignora status@broadcast (não tem msgs acessíveis)
        if chat_id == "status@broadcast":
            continue

        print(f"[{i+1}/{len(all_chats)}] 📱 {name or chat_id} ({ts_to_str(last_ts)})")

        messages = fetch_messages_for_chat(chat_id, SINCE_TIMESTAMP)
        print(f"   → {len(messages)} msg(s)")

        result.append({
            "chat_id":   chat_id,
            "chat_type": chat_type,
            "name":      name,
            "last_timestamp": last_ts,
            "messages_since_july": len(messages),
            "truncated": len(messages) >= MAX_MSGS_PER_CHAT,
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
            trunc_note = f" ⚠ TRUNCADO em {MAX_MSGS_PER_CHAT} msgs" if conv.get("truncated") else ""
            f.write(f"Mensagens no período: {conv['messages_since_july']}{trunc_note}\n")
            f.write(f"{'─'*80}\n")

            for msg in conv["messages"]:
                ts_str    = ts_to_str(msg.get("timestamp", 0))
                from_me   = msg.get("from_me", False)
                direction = "→ BOT" if from_me else "← LEAD"
                msg_type  = msg.get("type", "?")
                from_name = msg.get("from_name", "")
                from_num  = msg.get("from", "")

                sender = from_name or from_num or ("Bot" if from_me else "Lead")

                # Extrai conteúdo
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
        truncated = [c for c in result if c.get("truncated")]
        if truncated:
            f.write(f"CHATS TRUNCADOS ({MAX_MSGS_PER_CHAT} msgs max): {len(truncated)}\n")
            for t in truncated:
                f.write(f"  • {t['name'] or t['chat_id']}\n")

    print(f"📄 TXT salvo em: {OUTPUT_TXT}")

    total_msgs = sum(c["messages_since_july"] for c in result)
    truncated = [c for c in result if c.get("truncated")]
    print(f"\n📊 RESUMO FINAL:")
    print(f"  Conversas ativas desde 01/07: {len(result)}")
    print(f"  Total de mensagens coletadas: {total_msgs}")
    if truncated:
        print(f"  ⚠ Chats truncados ({MAX_MSGS_PER_CHAT} msgs): {len(truncated)}")
        for t in truncated:
            print(f"    • {t['name'] or t['chat_id']}")


if __name__ == "__main__":
    main()
