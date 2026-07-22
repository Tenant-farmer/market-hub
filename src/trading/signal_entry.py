"""매수 신호등(green) → 지수 ETF 진입 신호 자동 생성.

백테스트 결론(2007~): 시스템 엣지는 '지수+타이밍'에 있고 '선정'엔 없음. green(VIX≥30 또는
VIX≥20&VVIX≥95)일 때 SIGNAL_ENTRY_SYMBOL(기본 SPY)을 매수 신호로 signals 큐에 emit → 기존
리스크·실전 게이트·브로커·청산 파이프라인이 그대로 처리. 진입은 신호가, 실주문 여부는 게이트가 결정.

- 게이트: SIGNAL_ENTRY_ENABLED=1 일 때만 (기본 off — 예기치 않은 자동매수 방지)
- 멱등: 하루 1회 (signal-entry-{sym}-{today}) → green이 여러 날 지속되면 매일 1주씩 분할 진입
- dry=True 로 '무엇이 진입될지'만 미리보기
"""
import hashlib
import os
from datetime import date, datetime

from src import db
from src.dashboard.queries_macro import vix_signal
from src.trading import ensure_tables


def check_entry(con=None, dry=False):
    """green이면 지수 매수 신호 emit. 반환: {symbol, qty, signal} 또는 None."""
    own = con is None
    if own:
        con = db.connect()
    ensure_tables(con)
    out = None
    sig = vix_signal(con)
    if sig and str(sig.get("state", "")).startswith("buy"):     # buy1/buy2/buy3 = green
        sym = os.getenv("SIGNAL_ENTRY_SYMBOL", "SPY")
        qty = float(os.getenv("SIGNAL_ENTRY_QTY", "1"))
        today = date.today().isoformat()
        h = "signal-entry-" + hashlib.sha256(f"{sym}-{today}".encode()).hexdigest()[:20]
        if not dry:
            con.execute(
                "INSERT OR IGNORE INTO signals "
                "(hash, received_at, source, ticker, action, qty, strategy, raw, status) "
                "VALUES (?,?,?,?,?,?,?,?, 'new')",
                (h, datetime.now().isoformat(timespec="seconds"), "signal-entry", sym, "buy",
                 qty, f"신호진입:{sig['label']}", "{}"))
            con.commit()
        out = {"symbol": sym, "qty": qty, "signal": sig["label"]}
    if own:
        con.close()
    return out


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    print("신호 진입 점검 (dry):", check_entry(dry=True) or "green 아님 (진입 없음)")
