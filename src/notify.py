"""텔레그램 발송."""
import os

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def send(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정 — .env 확인")
    r = requests.post(
        API.format(token=token, method="sendMessage"),
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()
    return True


def discover_chat_id() -> str | None:
    """봇에게 먼저 말을 건 사용자의 chat_id를 getUpdates에서 찾는다."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 미설정 — .env 확인")
    r = requests.get(API.format(token=token, method="getUpdates"), timeout=20)
    r.raise_for_status()
    for upd in reversed(r.json().get("result", [])):
        msg = upd.get("message") or upd.get("edited_message")
        if msg and "chat" in msg:
            return str(msg["chat"]["id"])
    return None
