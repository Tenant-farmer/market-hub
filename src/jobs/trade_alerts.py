"""매매 체결 알림 — 우리가 실제로 사고팔 때 텔레그램 통지.

워커가 매 폴 사이클 끝에 호출. 아직 알림 안 보낸 주문(notified IS NULL)을 조회해
전략·방향별로 묶어 1건으로 발송(로테이션 8종목이 8통 아니라 1통) 후 notified=1 마킹.
- 상태 표기: filled/submitted/accepted=체결(예정), rejected/canceled/stale=실패/취소
- 멱등: notified 컬럼으로 재전송 방지. 텔레그램 미설정이면 조용히 스킵(마킹만)
"""
from datetime import datetime

STRAT = {"signal-entry": "🎯 신호진입", "rotation": "🔄 로테이션",
         "exit": "🛡 청산", "speed-test": "⚡ 테스트"}
OK = {"filled", "submitted", "accepted", "logged", "new", "partially_filled"}


def _ensure(con):
    try:
        con.execute("ALTER TABLE orders ADD COLUMN notified INTEGER")
        con.commit()
    except Exception:
        pass


def notify_new_orders(con) -> int:
    """미알림 주문을 묶어 발송, 발송 수 반환. 항상 notified 마킹(재전송 방지)."""
    _ensure(con)
    rows = con.execute(
        "SELECT id, created_at, broker, ticker, action, qty, price, status, "
        "COALESCE(strategy,'') strat FROM orders WHERE notified IS NULL "
        "AND created_at >= datetime('now','localtime','-1 day') ORDER BY id").fetchall()
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    # 전략 추출: orders.strategy 없으면 브로커명으로 대충 — 신호 소스가 정확
    groups: dict = {}
    for r in rows:
        src = r["strat"].split(":")[0] if r["strat"] else ""
        if not src:                                    # signals에서 소스 역추적
            sig = con.execute("SELECT source FROM signals WHERE ticker=? AND action=? "
                              "ORDER BY id DESC LIMIT 1", (r["ticker"], r["action"])).fetchone()
            src = sig["source"] if sig else "manual"
        groups.setdefault((src, r["action"]), []).append(r)

    sent = 0
    try:
        from src import notify

        for (src, action), items in groups.items():
            head = STRAT.get(src, f"📌 {src}")
            verb = "매수" if action == "buy" else "매도"
            L = [f"{head} {verb} {len(items)}건"]
            for r in items:
                mark = "✅" if r["status"] in OK else "❌"
                px = f" @{r['price']:,.2f}" if r["price"] else ""
                qty = f" x{r['qty']:g}" if r["qty"] else ""
                L.append(f"{mark} {r['ticker']}{qty}{px} · {r['status']}")
            notify.send("\n".join(L))
            sent += 1
    except Exception:
        pass                                           # 발송 실패해도 마킹은 진행(무한 재시도 방지)
    con.executemany("UPDATE orders SET notified=1 WHERE id=?", [(i,) for i in ids])
    con.commit()
    return sent


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")
    from src import db

    c = db.connect()
    print("발송:", notify_new_orders(c), "그룹")
    c.close()
