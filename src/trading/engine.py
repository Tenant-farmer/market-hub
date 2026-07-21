"""주문 엔진 — signals 큐 폴링: 리스크 게이트 → 실전 게이트 라우팅 → 상태 기록.

실전 게이트(trading_state.mode/armed):
- log        → 무조건 paper_log
- paper(기본) → 페이퍼/모의 브로커 (미국·크립토=Alpaca 페이퍼, KR=paper_log)
- live+armed  → 실전 브로커 (미구현 — 현재는 페이퍼로 안전 처리)
- live+미무장 → paper_log (로그만, 안전)
실행: python -m src.trading.engine  (1회) / 상시는 src.trading.worker
"""
from datetime import datetime

from src import db
from src.trading import ensure_tables, risk, state
from src.trading.brokers import alpaca
from src.trading.brokers.base import OrderRequest
from src.trading.brokers.paper_log import PaperLogBroker


def _pick_broker(ticker: str, st: dict):
    """(broker, note) — 실전 게이트 반영. 기본(paper)은 기존 동작 유지."""
    if st["mode"] == "log":
        return PaperLogBroker(), "log 모드"
    if st["mode"] == "live" and not st["armed"]:
        return PaperLogBroker(), "live 미무장(armed=0) → 로그만"
    if ticker.isdigit():                       # KR — 키움 어댑터 전까지 기록만
        return PaperLogBroker(), "KR 페이퍼(키움 대기)"
    if alpaca.configured():
        note = "alpaca-paper"
        if st["mode"] == "live":               # 실계좌 어댑터 미구현 → 페이퍼로 (안전)
            note += " (live 어댑터 미구현→페이퍼)"
        return alpaca.AlpacaBroker(), note
    return PaperLogBroker(), "alpaca 미설정"


def process_once(con=None) -> dict:
    own = con is None
    if own:
        con = db.connect()
    ensure_tables(con)
    st = state.get_state(con)
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
        broker, note = _pick_broker(sig["ticker"], st)
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
            (now, f"[{note}] {broker.name}: {res['status']}" + (" (중복)" if res.get("dup") else ""),
             sig["id"]),
        )
        done += 1
    con.commit()
    if own:
        con.close()
    return {"processed": done, "rejected": rejected}


if __name__ == "__main__":
    print(process_once())
