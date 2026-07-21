"""실전 게이트 상태 — mode(log/paper/live) + armed. DB 단일 행, 워커가 매 폴링 읽음.

- mode=log   : 모든 신호 paper_log (브로커 호출 없음) — 순수 파이프라인 테스트
- mode=paper : 페이퍼/모의 브로커 (Alpaca 페이퍼 등) — 기본값, 2주 무인 검증
- mode=live  : 실전 — armed=1 이어야 실주문. mode=live & armed=0 이면 로그만 (안전)
"""
from datetime import datetime

MODES = ("log", "paper", "live")


def get_state(con) -> dict:
    row = con.execute("SELECT mode, armed FROM trading_state WHERE id=1").fetchone()
    if not row:
        con.execute("INSERT OR IGNORE INTO trading_state (id, mode, armed) VALUES (1,'paper',0)")
        con.commit()
        return {"mode": "paper", "armed": 0}
    return {"mode": row["mode"], "armed": int(row["armed"])}


def set_mode(con, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode는 {MODES} 중 하나여야 함")
    con.execute(
        "UPDATE trading_state SET mode=?, updated_at=? WHERE id=1",
        (mode, datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()


def set_armed(con, armed: bool) -> None:
    con.execute(
        "UPDATE trading_state SET armed=?, updated_at=? WHERE id=1",
        (1 if armed else 0, datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()
