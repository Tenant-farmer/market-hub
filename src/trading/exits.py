"""청산 레이어 — 보유 포지션에 청산 규칙 적용 → SELL 신호 생성 (진입-청산 닫힌 루프).

규칙 (전부 기계적, env로 임계 조정):
- 손절      : 브로커 평가손익률 ≤ EXIT_STOP_PCT (기본 -8%)
- 추세 이탈 : 종가 < EXIT_MA일 이평 (기본 20MA) — 추세 붕괴
- 주도력 이탈: 종목 시장대비 RS(rs_mkt_21) < EXIT_RS (기본 0) — 대장 자격 상실 (analytics 있을 때만)

SELL은 signals 큐로 emit → 엔진이 게이트·리스크·브로커 경유 (buy와 동일 안전장치).
멱등: hash=exit-{종목}-{사유타입}-{날짜} → 같은 날 같은 사유 중복 매도 방지.
자동 실행은 EXIT_ENABLED=1 일 때만 (워커가 EXIT_CHECK_SEC 주기로). 기본 off — 테스트 중 예기치 않은 매도 방지.
dry=True 로 '무엇이 청산될지'만 미리보기 (신호 emit 안 함).
"""
import hashlib
import os
from datetime import date, datetime

from src import db
from src.trading import ensure_tables
from src.trading.brokers import alpaca, kiwoom


def _f(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _held(con) -> list:
    """보유 포지션 취합: KR(키움) + US(Alpaca). {code, qty, plpc}."""
    out = []
    if kiwoom.configured():
        try:
            bal = kiwoom.KiwoomBroker().account_balance()
            for h in (bal or {}).get("holdings", []):
                if h["qty"] > 0:
                    out.append({"code": h["code"], "qty": h["qty"], "plpc": h["plpc"]})
        except Exception:
            pass
    if alpaca.configured():
        try:
            pos = alpaca.AlpacaBroker().get_positions()
            for p in (pos if isinstance(pos, list) else []):
                q = float(p.get("qty", 0) or 0)
                if q > 0:
                    out.append({"code": p.get("symbol"), "qty": q,
                                "plpc": float(p.get("unrealized_plpc", 0) or 0) * 100})
        except Exception:
            pass
    return out


def _closes(con, code, n):
    try:
        rows = con.execute(
            "SELECT close FROM prices_daily WHERE symbol=? ORDER BY date DESC LIMIT ?", (code, n)
        ).fetchall()
        return [r["close"] for r in reversed(rows)]
    except Exception:
        return []


def _rs_mkt(con, code):
    scope = "kr_stock" if str(code).replace("/", "").isdigit() else "us_stock"
    try:
        r = con.execute(
            "SELECT value FROM analytics_daily WHERE scope=? AND code=? AND metric='rs_mkt_21' "
            "ORDER BY date DESC LIMIT 1", (scope, code),
        ).fetchone()
        return r["value"] if r else None
    except Exception:
        return None


def _eval(con, pos):
    """(사유 or None). 우선순위: 손절 → 추세이탈 → 주도이탈."""
    if pos["plpc"] is not None and pos["plpc"] <= _f("EXIT_STOP_PCT", -8.0):
        return f"손절 {pos['plpc']:+.1f}%"
    ma = int(_f("EXIT_MA", 20))
    c = _closes(con, pos["code"], ma + 5)
    if len(c) >= ma and c[-1] < sum(c[-ma:]) / ma:
        return f"추세이탈 종가<{ma}MA"
    rs = _rs_mkt(con, pos["code"])
    if rs is not None and rs < _f("EXIT_RS", 0.0):
        return f"주도이탈 RS{rs:+.0f}"
    return None


def _emit_sell(con, pos, reason):
    today = date.today().isoformat()
    key = reason.split()[0]  # 손절/추세이탈/주도이탈 — 사유타입만 멱등 키에
    h = "exit-" + hashlib.sha256(f"{pos['code']}-{key}-{today}".encode()).hexdigest()[:24]
    con.execute(
        "INSERT OR IGNORE INTO signals "
        "(hash, received_at, source, ticker, action, qty, strategy, raw, status) "
        "VALUES (?,?,?,?,?,?,?,?, 'new')",
        (h, datetime.now().isoformat(timespec="seconds"), "exit", pos["code"], "sell",
         pos["qty"], f"청산:{reason}", "{}"),
    )
    con.commit()


def check_exits(con=None, dry=False) -> list:
    """보유 포지션 청산 규칙 평가 → (dry가 아니면) SELL 신호 emit. 반환: 발생 리스트."""
    own = con is None
    if own:
        con = db.connect()
    ensure_tables(con)
    triggered = []
    for pos in _held(con):
        reason = _eval(con, pos)
        if reason:
            if not dry:
                _emit_sell(con, pos, reason)
            triggered.append({"code": pos["code"], "qty": pos["qty"], "reason": reason})
    if own:
        con.close()
    return triggered


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    dry = "--dry" in sys.argv
    print(f"청산 점검 (dry={dry}):")
    for t in check_exits(dry=dry):
        print(" ", t)
    print("완료")
