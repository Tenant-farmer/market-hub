"""리스크 게이트 — 엔진이 주문 직전에 통과시키는 검문소 (모든 모드 공통, 항상 적용).

- 킬스위치 (KILL_SWITCH=1): 즉시 전면 중단
- action 화이트리스트 / 팻핑거 수량 상한
- 주문 금액 상한 (KR=MAX_ORDER_KRW, US=MAX_ORDER_USD) — 오입력·폭주 방어
- 일일 주문 건수 상한 (MAX_DAILY_ORDERS) — 전략 오작동 시 서킷브레이커
- 일손실 한도 (MAX_DAILY_LOSS_PCT, 기본 2%) — 전일 에쿼티 대비 하루 손실 초과 시
  **신규 매수만 차단** (매도/청산은 항상 허용 — 손실 국면일수록 탈출은 열려 있어야 함).
  기준선 = portfolio_snapshots 전일 마지막 (없으면 오늘 첫), 현재값 = 라이브 잔고(60초 캐시).
  잔고 조회 실패 시 통과 (fail-open — 페이퍼 검증용; 실전 전환 시 fail-closed 재검토)
"""
import os
import time
from datetime import date

ALLOWED_ACTIONS = {"buy", "sell"}
MAX_QTY = 100000                    # 명백한 오입력 차단 (팻핑거)
MAX_ORDER_USD = 10000.0            # 주문당 상한 (미국·크립토)
MAX_ORDER_KRW = 10000000.0        # 주문당 상한 (국내, 1천만원)
MAX_DAILY_ORDERS = 20              # 하루 주문 건수 상한 (서킷브레이커)
MAX_DAILY_LOSS_PCT = 2.0           # 일손실 한도 (계좌 %, 0이면 비활성)

_eq_cache: dict = {}               # {broker: (monotonic, equity)} — 연속 주문 시 잔고 재조회 방지


def _f(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _live_equity(broker: str):
    hit = _eq_cache.get(broker)
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    cur = None
    try:
        if broker == "kiwoom":
            from src.trading.brokers import kiwoom

            if kiwoom.configured():
                b = kiwoom.KiwoomBroker().account_balance()
                cur = (b or {}).get("cash") or None      # 추정예탁자산 = 총자산
        else:
            from src.trading.brokers import alpaca

            if alpaca.configured():
                a = alpaca.AlpacaBroker().get_account()
                cur = float(a.get("equity") or 0) or None
    except Exception:
        cur = None
    _eq_cache[broker] = (time.monotonic(), cur)
    return cur


def _daily_loss_blocked(con, is_kr: bool):
    """일손실 한도 초과 시 사유 문자열, 아니면 None (신규 매수에만 적용)."""
    pct = _f("MAX_DAILY_LOSS_PCT", MAX_DAILY_LOSS_PCT)
    if pct <= 0:
        return None
    broker = "kiwoom" if is_kr else "alpaca"
    today = date.today().isoformat()
    try:                            # 기준선: 전일 마지막 스냅샷 (없으면 오늘 첫)
        base = con.execute(
            "SELECT equity FROM portfolio_snapshots WHERE broker=? AND date<? "
            "ORDER BY date DESC LIMIT 1", (broker, today)).fetchone()
        if not base:
            base = con.execute(
                "SELECT equity FROM portfolio_snapshots WHERE broker=? AND date=?",
                (broker, today)).fetchone()
    except Exception:
        return None
    if not base or not base["equity"]:
        return None
    cur = _live_equity(broker)
    if not cur:
        return None
    loss = (cur / base["equity"] - 1) * 100
    if loss <= -pct:
        msg = f"일손실 한도 초과({broker}): {loss:+.2f}% ≤ -{pct:g}% — 신규 매수 차단"
        _alert_daily(con, f"daily_loss_{broker}", f"⛔ {msg}")
        return msg
    return None


def _alert_daily(con, kind: str, text: str):
    """같은 종류 경보는 12시간에 1회만 텔레그램 발신."""
    try:
        dup = con.execute(
            "SELECT 1 FROM collector_runs WHERE collector='risk' AND message=? "
            "AND run_at >= datetime('now','localtime','-720 minutes') LIMIT 1",
            (kind,)).fetchone()
        if dup:
            return
        con.execute("INSERT INTO collector_runs (collector, run_at, status, rows, message) "
                    "VALUES ('risk', datetime('now','localtime'), 'alert', 0, ?)", (kind,))
        con.commit()
        from src import notify

        notify.send(text)
    except Exception:
        pass


def check(con, sig) -> tuple[bool, str]:
    """(허용 여부, 사유). sig는 signals 테이블 row."""
    if os.getenv("KILL_SWITCH", "") == "1":
        return False, "킬스위치 활성 (KILL_SWITCH=1)"
    if not sig["ticker"]:
        return False, "티커 없음"
    if sig["action"] not in ALLOWED_ACTIONS:
        return False, f"허용되지 않은 action: {sig['action']}"

    qty = sig["qty"]
    if qty is not None and not (0 < qty <= MAX_QTY):
        return False, f"수량 범위 밖: {qty}"

    # 주문 금액 상한 (가격이 있을 때만 — 시장가 의도는 수량 상한으로 커버)
    if qty and sig["price"]:
        notional = qty * sig["price"]
        is_kr = str(sig["ticker"]).isdigit()
        cap = _f("MAX_ORDER_KRW", MAX_ORDER_KRW) if is_kr else _f("MAX_ORDER_USD", MAX_ORDER_USD)
        if notional > cap:
            unit = "KRW" if is_kr else "USD"
            return False, f"주문금액 상한 초과: {notional:,.0f} > {cap:,.0f} {unit}"

    # 일일 주문 건수 서킷브레이커 (오늘 이미 나간 주문 수)
    cap_n = int(_f("MAX_DAILY_ORDERS", MAX_DAILY_ORDERS))
    today = date.today().isoformat()
    n = con.execute(
        "SELECT COUNT(*) c FROM orders WHERE substr(created_at,1,10)=?", (today,)
    ).fetchone()["c"]
    if n >= cap_n:
        return False, f"일일 주문 상한 도달: {n}/{cap_n} (서킷브레이커)"

    # 일손실 한도 — 신규 매수만 차단 (매도/청산은 항상 통과)
    if sig["action"] == "buy":
        why = _daily_loss_blocked(con, str(sig["ticker"]).isdigit())
        if why:
            return False, why

    return True, "ok"
