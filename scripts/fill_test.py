"""자동매매 파이프라인 체결 검증 — 신호 삽입 → 엔진 처리 → 브로커 체결까지 폴링.

이전 임시 스크립트는 접수 직후(6초)에 확인해 미체결로 오판했음(개장 시 페이퍼 체결이 ~2분 소요).
이 버전은 종료상태(filled/canceled/rejected)까지 최대 --poll-min분 폴링. KR(키움)은 잔고 반영으로 확인.

실행: python scripts/fill_test.py --sym AAPL --side buy --qty 1 [--wait-open] [--poll-min 5]
"""
import argparse
import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from src import db
from src.trading import engine, ensure_tables
from src.trading.brokers import alpaca, kiwoom


def _now():
    return datetime.now().strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default="AAPL")
    ap.add_argument("--side", default="buy", choices=["buy", "sell"])
    ap.add_argument("--qty", type=float, default=1)
    ap.add_argument("--wait-open", action="store_true", help="미국장 개장까지 대기")
    ap.add_argument("--poll-min", type=float, default=5, help="체결 폴링 최대 분")
    a = ap.parse_args()
    is_kr = a.sym.isdigit()

    con = db.connect()
    ensure_tables(con)

    if a.wait_open and not is_kr:
        b = alpaca.AlpacaBroker()
        while not b.is_market_open(a.sym):
            print(_now(), "미국장 개장 대기...")
            time.sleep(60)
        print(_now(), "★ 개장 확인")

    h = "test-" + hashlib.sha256(
        f"{a.sym}-{a.side}-{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:20]
    con.execute(
        "INSERT INTO signals (hash,received_at,source,ticker,action,qty,strategy,raw,status) "
        "VALUES (?,?,?,?,?,?,?,?, 'new')",
        (h, datetime.now().isoformat(timespec="seconds"), "test", a.sym, a.side,
         a.qty, "fill-test", "{}"))
    con.commit()
    print(_now(), f"신호 삽입: {h} ({a.side} {a.sym} x{a.qty:g})")

    print(_now(), "엔진 처리:", engine.process_once(con))

    coid = "sig-" + h[:20]
    deadline = time.time() + a.poll_min * 60
    filled = False
    while time.time() < deadline:
        if is_kr:
            try:
                bal = kiwoom.KiwoomBroker().account_balance()
                hold = next((x for x in (bal or {}).get("holdings", []) if x["code"] == a.sym), None)
                print(_now(), "키움 잔고:", hold or "미보유")
                if hold and hold["qty"] > 0:
                    filled = True
                    break
            except Exception as e:
                print(_now(), "잔고조회 err:", str(e)[:80])
        else:
            st = alpaca.AlpacaBroker().order_status(coid)
            print(_now(), f"주문상태: {st.get('status')} 체결 {st.get('filled_qty')} "
                          f"@ {st.get('filled_avg_price')}")
            if st.get("status") in ("filled", "canceled", "rejected", "expired"):
                filled = st.get("status") == "filled"
                break
        time.sleep(15)

    print(_now(), "=== 체결 확인 ===" if filled else "=== 미체결/타임아웃 (장중 아님이거나 지연) ===")
    con.close()


if __name__ == "__main__":
    main()
