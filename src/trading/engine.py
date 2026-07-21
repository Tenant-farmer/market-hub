"""주문 엔진 — signals 큐 폴링: 리스크 게이트 → 브로커 라우팅 → 상태 기록.

paper_log 단계: 모든 신호를 PaperLogBroker로 보낸다 (주문 의도만 기록).
실브로커 연결 시 settings [trading].armed와 티커 형태(6자리=KR)로 라우팅 확장.
실행: python -m src.trading.engine  (1회 처리 후 종료 — 상시 데몬은 브로커 연결 단계에서)
"""
from datetime import datetime

from src import db
from src.trading import ensure_tables, risk
from src.trading.brokers.base import OrderRequest
from src.trading.brokers.paper_log import PaperLogBroker


def process_once(con=None) -> dict:
    own = con is None
    if own:
        con = db.connect()
    ensure_tables(con)
    broker = PaperLogBroker()
    done = rejected = 0
    for sig in con.execute("SELECT * FROM signals WHERE status='new' ORDER BY id").fetchall():
        ok, reason = risk.check(con, sig)
        now = datetime.now().isoformat(timespec="seconds")
        if not ok:
            con.execute(
                "UPDATE signals SET status='rejected', processed_at=?, result=? WHERE id=?",
                (now, reason, sig["id"]),
            )
            rejected += 1
            continue
        res = broker.submit_order(
            con,
            OrderRequest(
                ticker=sig["ticker"], action=sig["action"],
                qty=sig["qty"], price=sig["price"], strategy=sig["strategy"] or "",
            ),
            client_order_id="sig-" + sig["hash"][:20],
            signal_id=sig["id"],
        )
        con.execute(
            "UPDATE signals SET status='processed', processed_at=?, result=? WHERE id=?",
            (now, f"{broker.name}: {res['status']}" + (" (중복)" if res.get("dup") else ""), sig["id"]),
        )
        done += 1
    con.commit()
    if own:
        con.close()
    return {"processed": done, "rejected": rejected}


if __name__ == "__main__":
    print(process_once())
