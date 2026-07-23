"""트레이딩뷰 웹훅 수신기 — POST /hook/tv.

TV 3초 룰: 검증 + signals INSERT 후 즉시 200. 주문 처리는 **적재 직후 별도 스레드가 즉시 실행**
(시차 ~0.1-1초) + 워커 폴링(15초)이 백업 스위퍼 — 스레드가 실패해도 신호는 유실되지 않음.
페이로드 예 (TV 알림 메시지에 JSON으로 작성):
  {"secret": "...", "ticker": "AAPL", "action": "buy", "qty": 1,
   "price": {{close}}, "strategy": "breakout", "time": "{{timenow}}"}
- secret: .env의 WEBHOOK_SECRET와 일치해야 함
- "time"에 {{timenow}}를 넣어야 같은 조건의 다른 시점 알림이 구분됨
  (멱등키 = sha256(secret 제외 payload + UTC날짜) — 재전송은 걸러지고 다른 날 신호는 통과)
"""
import hashlib
import hmac
import json
import os
import threading
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from src import db
from src.trading import ensure_tables

bp = Blueprint("hook", __name__)

_proc_lock = threading.Lock()


def _process_now():
    """적재된 신호 즉시 처리 (별도 스레드) — 실패해도 워커가 15초 내 백업 처리."""
    if not _proc_lock.acquire(blocking=False):
        return                                    # 이미 처리 중인 스레드가 큐를 비움
    try:
        from src.trading import engine

        engine.process_once()
    except Exception:
        pass
    finally:
        _proc_lock.release()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@bp.post("/hook/tv")
def tv_hook():
    secret = os.getenv("WEBHOOK_SECRET", "")
    if not secret:
        return jsonify({"ok": False, "error": "receiver not configured"}), 503
    data = request.get_json(silent=True) or {}
    if not hmac.compare_digest(str(data.get("secret", "")), secret):
        return jsonify({"ok": False}), 403

    ticker = str(data.get("ticker", "")).strip().upper()
    action = str(data.get("action", "")).strip().lower()
    if not ticker or action not in ("buy", "sell"):
        return jsonify({"ok": False, "error": "invalid payload"}), 400

    payload = {k: v for k, v in data.items() if k != "secret"}
    day = datetime.now(timezone.utc).date().isoformat()
    sig_hash = hashlib.sha256(
        (json.dumps(payload, sort_keys=True, ensure_ascii=False) + day).encode()
    ).hexdigest()

    con = db.connect()
    ensure_tables(con)
    cur = con.execute(
        "INSERT OR IGNORE INTO signals "
        "(hash, received_at, source, ticker, action, qty, price, strategy, raw) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            sig_hash, datetime.now().isoformat(timespec="seconds"),
            str(data.get("source", "tv")), ticker, action,
            _num(data.get("qty")), _num(data.get("price")),
            str(data.get("strategy", ""))[:60],
            json.dumps(payload, ensure_ascii=False)[:2000],
        ),
    )
    con.commit()
    dup = cur.rowcount == 0
    con.close()
    if not dup and not current_app.testing:       # 새 신호 → 즉시 처리 (폴링 시차 제거)
        threading.Thread(target=_process_now, daemon=True).start()
    return jsonify({"ok": True, "dup": dup}), 200
