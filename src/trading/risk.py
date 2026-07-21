"""리스크 게이트 — 엔진이 주문 직전에 통과시키는 검문소.

paper_log 단계: 킬스위치 + 페이로드 정합성만. 실브로커 연결 시
최대 포지션 %, 일손실 한도, 장시간 체크가 여기에 추가된다 (실전 게이트 조건).
"""
import os

ALLOWED_ACTIONS = {"buy", "sell"}
MAX_QTY = 100000   # 명백한 오입력 차단 (팻핑거)


def check(con, sig) -> tuple[bool, str]:
    """(허용 여부, 사유). sig는 signals 테이블 row."""
    if os.getenv("KILL_SWITCH", "") == "1":
        return False, "킬스위치 활성 (KILL_SWITCH=1)"
    if not sig["ticker"]:
        return False, "티커 없음"
    if sig["action"] not in ALLOWED_ACTIONS:
        return False, f"허용되지 않은 action: {sig['action']}"
    if sig["qty"] is not None and not (0 < sig["qty"] <= MAX_QTY):
        return False, f"수량 범위 밖: {sig['qty']}"
    return True, "ok"
