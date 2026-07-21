"""리스크 게이트 — 엔진이 주문 직전에 통과시키는 검문소 (모든 모드 공통, 항상 적용).

- 킬스위치 (KILL_SWITCH=1): 즉시 전면 중단
- action 화이트리스트 / 팻핑거 수량 상한
- 주문 금액 상한 (KR=MAX_ORDER_KRW, US=MAX_ORDER_USD) — 오입력·폭주 방어
- 일일 주문 건수 상한 (MAX_DAILY_ORDERS) — 전략 오작동 시 서킷브레이커
실브로커 연결 시: 일손실 한도(계좌 P&L 필요)를 여기에 추가 (실전 게이트 조건).
"""
import os
from datetime import date

ALLOWED_ACTIONS = {"buy", "sell"}
MAX_QTY = 100000                    # 명백한 오입력 차단 (팻핑거)
MAX_ORDER_USD = 10000.0            # 주문당 상한 (미국·크립토)
MAX_ORDER_KRW = 10000000.0        # 주문당 상한 (국내, 1천만원)
MAX_DAILY_ORDERS = 20              # 하루 주문 건수 상한 (서킷브레이커)


def _f(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


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

    return True, "ok"
