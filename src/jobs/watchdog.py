"""무인 가동 상호 감시 — 상시 프로세스가 죽으면 텔레그램으로 즉시 알림.

- hourly → check_engine : 엔진 하트비트(collector_runs 'engine')가 ENGINE_STALL_MIN(기본 45분) 없으면 경보
- worker → check_hourly : 시간별 수집('sentiment')이 HOURLY_STALL_MIN(기본 150분) 없으면 경보
서로를 감시하므로 한쪽이 살아있는 한 장애를 놓치지 않는다. 둘 다(PC 자체가) 죽으면
아침 브리핑 부재로 인지. 경보는 종류당 ALERT_COOLDOWN_H(기본 6시간) 1회만(스팸 방지,
collector_runs 'watchdog' 기록으로 중복 판정).
"""
import os
from datetime import datetime


def _stalled(con, collector: str, minutes: float) -> bool:
    return con.execute(
        "SELECT 1 FROM collector_runs WHERE collector=? AND status='ok' "
        "AND run_at >= datetime('now','localtime',?) LIMIT 1",
        (collector, f"-{int(minutes)} minutes"),
    ).fetchone() is None


def _alert_once(con, kind: str, text: str) -> bool:
    cool_min = float(os.getenv("ALERT_COOLDOWN_H", "6")) * 60
    dup = con.execute(
        "SELECT 1 FROM collector_runs WHERE collector='watchdog' AND message=? "
        "AND run_at >= datetime('now','localtime',?) LIMIT 1",
        (kind, f"-{int(cool_min)} minutes"),
    ).fetchone()
    if dup:
        return False
    try:
        from src import notify

        notify.send(text)
    except Exception:
        pass                                    # 텔레그램 실패해도 기록은 남김
    con.execute(
        "INSERT INTO collector_runs (collector, run_at, status, rows, message) "
        "VALUES ('watchdog', ?, 'ok', 0, ?)",
        (datetime.now().isoformat(timespec="seconds"), kind),
    )
    con.commit()
    return True


def check_engine(con) -> int:
    """hourly에서 호출 — 엔진 워커 생존 확인. 경보 발송 시 1."""
    m = float(os.getenv("ENGINE_STALL_MIN", "45"))
    if _stalled(con, "engine", m):
        return int(_alert_once(
            con, "engine_stall",
            f"🚨 워치독: 엔진 워커 하트비트가 {int(m)}분째 없음 — market-hub-engine 확인 필요"))
    return 0


def check_hourly(con) -> int:
    """엔진 워커에서 호출 — 시간별 수집 생존 확인. 경보 발송 시 1."""
    m = float(os.getenv("HOURLY_STALL_MIN", "150"))
    if _stalled(con, "sentiment", m):
        return int(_alert_once(
            con, "hourly_stall",
            f"🚨 워치독: 시간별 수집이 {int(m)}분째 없음 — market-hub-hourly 확인 필요"))
    return 0
