"""주문 체결 상태 동기화 — orders의 비종료 주문을 브로커에서 조회해 최종상태로 갱신.

엔진은 제출 시점 상태(pending_new 등)만 기록하고 넘어감 → 이후 체결/취소가 orders에 반영 안 됨
(AAPL 검증 때 발견). 이 폴러가 Alpaca order_status로 filled/canceled 등을 반영. 읽기+상태갱신뿐이라
주문을 내지 않음(안전) → 워커에서 게이트 없이 주기 실행. KR(키움)은 상태조회 API 미연동 →
/positions의 실시간 잔고로 확인(추후 kt00007 등 연동 시 확장).
"""
from src import db
from src.trading import ensure_tables
from src.trading.brokers import alpaca

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
    if own:
        con.close()
    return updated


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    up = reconcile()
    print(f"체결반영 {len(up)}건:", up or "변경 없음")
