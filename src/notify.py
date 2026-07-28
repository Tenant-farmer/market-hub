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
    if not r.ok:
        # raise_for_status()는 **URL을 그대로 담아** 봇 토큰이 로그·트레이스백에 새어나간다
        # (2026-07-28 실측: 400 응답 한 번에 토큰이 콘솔에 노출됨).
        # 토큰을 지우고 텔레그램이 준 사유만 남긴다.
        try:
            why = r.json().get("description", "")[:200]
        except Exception:
            why = r.text[:200]
        raise RuntimeError(f"텔레그램 발송 실패 {r.status_code}: {why}")
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
