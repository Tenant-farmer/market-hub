"""청산 레이어 — 보유 포지션에 청산 규칙 적용 → SELL 신호 생성 (진입-청산 닫힌 루프).

규칙 (전부 기계적, env로 임계 조정). **전략 성격에 따라 다른 규칙을 쓴다**:
- 손절      : 평가손익률 ≤ US `EXIT_STOP_PCT`(-15) / KR `EXIT_STOP_PCT_KR`(-25).
              시장별로 다른 이유는 _stop_pct 참조(KR 변동성이 US의 2.2배).
              -8%는 2026-07-27 검증에서 **낙폭을 오히려 키우는 것**으로 드러나 폐기
- 추세 이탈 : 종가 < EXIT_MA일 이평 (기본 20MA) — **기본 비활성**(EXIT_MA_ENABLED=1로 켬).
              백테스트(2007~)상 일별 MA-크로스 청산은 휩쏘로 순수 해로움(+28%→제거시 +152%) → 기본 off
- 주도력 이탈: 종목 시장대비 RS(rs_mkt_21) < EXIT_RS (기본 0) — 대장 자격 상실 (analytics 있을 때만)
- 시간 청산 : **신호진입(역발상 매수)만** — 63거래일 경과 시. 이 포지션엔 손절·주도이탈을
              적용하지 않는다(공포에 사는 전략에 손절은 자기모순, _contrarian 참조)

SELL은 signals 큐로 emit → 엔진이 게이트·리스크·브로커 경유 (buy와 동일 안전장치).
멱등: hash=exit-{종목}-{사유타입}-{날짜} → 같은 날 같은 사유 중복 매도 방지.
자동 실행은 EXIT_ENABLED=1 일 때만 (워커가 EXIT_CHECK_SEC 주기로). 기본 off — 테스트 중 예기치 않은 매도 방지.
dry=True 로 '무엇이 청산될지'만 미리보기 (신호 emit 안 함).
"""
import hashlib
import os
from datetime import date, datetime

from src import db
from src.errlog import swallow
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
                    out.append({"code": h["code"], "qty": h["qty"], "plpc": h["plpc"],
                                "px": h.get("cur")})
        except Exception as e:
            # 잔고 조회 실패 = KR 보유가 통째로 빠짐 = **손절이 안 돈다**. 반드시 흔적을 남긴다
            swallow("exits.held.kiwoom", e)
    if alpaca.configured():
        try:
            pos = alpaca.AlpacaBroker().get_positions()
            for p in (pos if isinstance(pos, list) else []):
                q = float(p.get("qty", 0) or 0)
                if q > 0:
                    out.append({"code": p.get("symbol"), "qty": q,
                                "plpc": float(p.get("unrealized_plpc", 0) or 0) * 100,
                                "px": float(p.get("current_price", 0) or 0) or None})
        except Exception as e:
            swallow("exits.held.alpaca", e)
    try:
        # 로테이션 슬롯은 자체 이탈규칙(rank>30, 주1회)이 관리 → 추세·주도이탈 청산에서 제외.
        # **단 손절은 제외하지 않는다** — 주1회 평가 사이의 폭락에 무방비였던 실사고
        # (2026-07-27: SK이터닉스 -25.2%·티에스이 -11.2%가 손절 없이 방치, thesis 점검이 발견).
        # 로테이션 백테스트에도 손절이 없었으므로 이는 백테스트 대비 '더 보수적'인 안전장치.
        rot = {r["symbol"] for r in con.execute("SELECT symbol FROM rotation_slots")}
        out = [p for p in out if p["code"] not in rot or p["plpc"] <= _stop_pct(p["code"])]
    except Exception as e:
        swallow("exits.rotation_filter", e)
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


def _stop_pct(code) -> float:
    """시장별 손절폭 — KR은 변동성이 US의 2.2배라 같은 숫자를 쓰면 안 된다.

    실측(2024~, 2026-07-27): 연율변동성 중앙 KR 64.3% vs US 29.5%.
    하루 -8% 이상 하락 빈도도 KR 2.41% vs US 0.45%로 **5.4배** — 같은 -8%가 KR에선
    41거래일에 한 번, US에선 222거래일에 한 번 걸린다. 전혀 다른 규칙이 되는 셈.

    US 값(-15%)은 496종목·11.6년 + 소멸종목 주입 스트레스로 검증됐다(scripts/stop_loss_sweep.py).
    **KR 값은 변동성 비율로 스케일한 추정치이며 검증되지 않았다** — KR 로테이션 백테스트는
    기저 전략 자체가 손실(CAGR -3~-5%)이라 손절폭을 최적화할 근거를 주지 못했다.
    """
    kr = str(code).isdigit()
    return _f("EXIT_STOP_PCT_KR", -25.0) if kr else _f("EXIT_STOP_PCT", -15.0)


SIGNAL_HOLD_DAYS = 63          # 신호진입 보유기간 (백테스트 원 설계: 승률 81.8% / 평균 +15.1%)


def _contrarian(code) -> bool:
    """신호진입(역발상 매수) 대상인가 — 손절·주도이탈을 적용하면 안 되는 포지션.

    KR 신호진입은 'VKOSPI≥30 & 낙폭-5%면 산다' = **공포에 산다**가 논거인데, 여기에 손절을
    걸면 '더 떨어지면 판다'가 되어 전략과 정면 충돌한다. 실측(scripts/signal_entry_stop.py,
    KOSPI 2010~26, green 215일): 손절 없음 평균 +15.12%·승률 81.8% / -8% 평균 +10.45%·
    승률 66.4% / -5% 평균 +8.81%·승률 50.9% — **손절이 좁을수록 단조롭게 나빠진다**.
    지수는 0으로 가지 않으므로 '파산 회피'라는 손절 본래 목적도 성립하지 않는다.

    대신 시간청산(63거래일)을 쓴다 — 백테스트가 검증한 것이 그 보유기간이다.
    """
    from src.trading import signal_entry

    return str(code) in signal_entry.symbols()


def _signal_matured(con, code):
    """신호진입 포지션이 보유기간을 채웠나 → (True, 경과일) / (False, 경과일).

    분할매수라 트랜치마다 시계가 다르다. **마지막 매수 기준**으로 세어 모든 트랜치가
    최소 63거래일을 채우게 한다(보수적 근사 — 트랜치별 장부는 과하다).
    """
    r = con.execute(
        "SELECT MAX(o.created_at) d FROM orders o JOIN signals s ON s.id = o.signal_id "
        "WHERE s.source='signal-entry' AND o.ticker=? AND o.action='buy' AND o.status='filled'",
        (str(code),)).fetchone()
    if not r or not r["d"]:
        return False, 0
    n = con.execute(
        "SELECT COUNT(DISTINCT date) n FROM prices_daily WHERE symbol=? AND date > ?",
        (str(code), r["d"][:10])).fetchone()["n"]
    return n >= SIGNAL_HOLD_DAYS, n


def _eval(con, pos):
    """(사유 or None). 우선순위: 손절 → 추세이탈 → 주도이탈.

    단 신호진입 포지션은 손절·주도이탈을 건너뛰고 **시간청산만** 적용한다(_contrarian 참조).
    """
    if _contrarian(pos["code"]):
        matured, n = _signal_matured(con, pos["code"])
        return f"신호진입 보유만료 {n}거래일" if matured else None
    if pos["plpc"] is not None and pos["plpc"] <= _stop_pct(pos["code"]):
        return f"손절 {pos['plpc']:+.1f}%"
    if os.getenv("EXIT_MA_ENABLED") == "1":            # 백테스트상 해로워 기본 off
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
    # ref_price = 판단 시점의 현재가. 체결가와 비교해야 순수 슬리피지가 나온다
    # (전일 종가 대비로 재면 갭이 슬리피지로 오인됨 — 2026-07-27 SK이터닉스 -29% 사례)
    from src.analytics import slippage

    slippage.ensure(con)
    con.execute(
        "INSERT OR IGNORE INTO signals "
        "(hash, received_at, source, ticker, action, qty, strategy, raw, status, ref_price) "
        "VALUES (?,?,?,?,?,?,?,?, 'new', ?)",
        (h, datetime.now().isoformat(timespec="seconds"), "exit", pos["code"], "sell",
         pos["qty"], f"청산:{reason}", "{}", slippage.emit_ref(con, pos["code"], pos.get("px"))),
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
