"""주문 체결 상태 동기화 — orders의 비종료 주문을 브로커에서 조회해 최종상태로 갱신.

엔진은 제출 시점 상태(pending_new/submitted)만 기록하고 넘어감 → 이후 체결이 orders에 반영 안 됨.
- Alpaca: order_status(client_order_id)로 filled/canceled 반영
- 키움: kt00007 주문체결내역(주문번호 매칭)으로 체결 반영 + **매도 워치독** — 매도가
  KIWOOM_SELL_TIMEOUT_SEC(기본 120초) 넘게 미체결이면 원주문 취소(kt10003) 후 재제출 신호 emit
  (멱등: 원주문당 1회, sell-retry-<coid>). 손절이 조용히 안 팔리는 사고 방지 + 텔레그램 경보
동기화 자체는 주문을 내지 않음(재제출도 signals 큐 경유 → 리스크·실전 게이트 통과) → 워커에서 상시 실행.
"""
import hashlib
import os
from datetime import datetime

from src import db
from src.trading import ensure_tables
from src.trading.brokers import alpaca, kiwoom

TERMINAL = ("filled", "canceled", "cancelled", "rejected", "expired", "done_for_day")


def reconcile(con=None):
    """비종료 Alpaca 주문의 상태를 갱신. 반환: 변경된 주문 리스트."""
    own = con is None
    if own:
        con = db.connect()
    ensure_tables(con)
    updated = []
    if alpaca.configured():
        b = alpaca.AlpacaBroker()
        rows = con.execute(
            "SELECT client_order_id, status FROM orders "
            "WHERE broker='alpaca' AND client_order_id IS NOT NULL "
            "AND status NOT IN ('filled','canceled','rejected','expired') "
            "AND status NOT LIKE 'http_%' "            # 제출 실패/미존재 주문은 재폴링 제외
            "AND created_at >= date('now','-7 days')"
        ).fetchall()
        for r in rows:
            o = b.order_status(r["client_order_id"])
            ns = o.get("status")
            if ns and ns != r["status"]:
                msg = (f"filled {o.get('filled_qty')}@{o.get('filled_avg_price')}"
                       if ns == "filled" else str(o.get("id", ""))[:80])
                con.execute("UPDATE orders SET status=?, message=? WHERE client_order_id=?",
                            (ns, msg, r["client_order_id"]))
                updated.append({"coid": r["client_order_id"], "from": r["status"], "to": ns})
        con.commit()
    if kiwoom.configured():
        updated += _kiwoom_sync(con)
    if own:
        con.close()
    return updated


def _age_sec(created_at: str) -> float:
    try:
        return (datetime.now() - datetime.fromisoformat(created_at)).total_seconds()
    except (ValueError, TypeError):
        return 0.0


def _alert(text: str):
    try:
        from src import notify

        notify.send(text)
    except Exception:
        pass


def _kiwoom_sync(con) -> list:
    """키움 주문 체결 반영(kt00007 주문번호 매칭) + 매도 미체결 워치독."""
    rows = con.execute(
        "SELECT client_order_id, ticker, action, qty, status, message, created_at FROM orders "
        "WHERE broker LIKE 'kiwoom%' AND status='submitted' "
        "AND created_at >= datetime('now','localtime','-1 day')").fetchall()
    if not rows:
        return []
    b = kiwoom.KiwoomBroker()
    hist = {h["ord_no"]: h for h in b.order_history() if h["ord_no"]}
    timeout = float(os.getenv("KIWOOM_SELL_TIMEOUT_SEC", "120"))
    updated = []
    for r in rows:
        coid = r["client_order_id"]
        ord_no = (r["message"] or "").split(" ")[0] if r["message"] else ""
        h = hist.get(ord_no)
        age = _age_sec(r["created_at"])
        if h and h["remain"] == 0 and h["filled"] > 0:
            con.execute("UPDATE orders SET status='filled', message=? WHERE client_order_id=?",
                        (f"{ord_no} filled {h['filled']:g}@{h['price']:g}", coid))
            updated.append({"coid": coid, "from": "submitted", "to": "filled"})
            continue
        # 취소 실측 시그니처: 원주문이 mdfy '일반'인 채 체결 0·미체결 0으로 소멸
        # (취소 마크는 별도 취소주문 레코드에 붙음). 갓 접수된 주문 오판 방지로 age 가드
        if h and ("취소" in (h["mdfy"] or "")
                  or (h["remain"] == 0 and h["filled"] == 0 and age >= 30)):
            con.execute("UPDATE orders SET status='canceled', message=? WHERE client_order_id=?",
                        (f"{ord_no} 취소/소멸", coid))
            updated.append({"coid": coid, "from": "submitted", "to": "canceled"})
            continue
        # 미체결(remain>0) 또는 이력에 아예 없음 → 매도는 워치독 (손절이 조용히 죽는 사고 방지)
        if r["action"] == "sell" and age >= timeout and b.is_market_open(r["ticker"]):
            if h and h["remain"] > 0:                      # 걸려있으면 취소 먼저 (이중매도 방지)
                cr = b.cancel_order(ord_no, r["ticker"])
                if not cr["ok"]:                            # 취소 실패(그 사이 체결 등) → 다음 사이클
                    continue
            rh = "sell-retry-" + hashlib.sha256(coid.encode()).hexdigest()[:20]
            cur = con.execute(
                "INSERT OR IGNORE INTO signals "
                "(hash, received_at, source, ticker, action, qty, strategy, raw, status) "
                "VALUES (?,?,?,?,?,?,?,?, 'new')",
                (rh, datetime.now().isoformat(timespec="seconds"), "sell-retry", r["ticker"],
                 "sell", r["qty"], f"매도재제출:{ord_no or '이력없음'}", "{}"))
            if cur.rowcount:                                # 원주문당 1회만 (멱등)
                con.execute("UPDATE orders SET status='stale_replaced', message=? "
                            "WHERE client_order_id=?",
                            (f"{ord_no} {int(age)}초 미체결 → 취소·재제출", coid))
                updated.append({"coid": coid, "from": "submitted", "to": "stale_replaced"})
                _alert(f"⚠ 매도 워치독: {r['ticker']} 주문 {ord_no or '(이력없음)'} "
                       f"{int(age)}초 미체결 → 취소 후 재제출")
    con.commit()
    return updated


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    up = reconcile()
    print(f"체결반영 {len(up)}건:", up or "변경 없음")
