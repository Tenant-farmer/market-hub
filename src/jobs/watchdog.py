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
        "AND run_at >= replace(datetime('now','localtime',?),' ','T') LIMIT 1",
        (collector, f"-{int(minutes)} minutes"),
    ).fetchone() is None


def _alert_once(con, kind: str, text: str) -> bool:
    cool_min = float(os.getenv("ALERT_COOLDOWN_H", "6")) * 60
    dup = con.execute(
        "SELECT 1 FROM collector_runs WHERE collector='watchdog' AND message=? "
        "AND run_at >= replace(datetime('now','localtime',?),' ','T') LIMIT 1",
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
        # status='alert' — 판정·리포트가 경보를 셀 수 있게 한다. 이전엔 'ok'로 기록해
        # verdict가 "경보 0건 ✅"로 보고했다(2026-07-28 실사고: 엔진 2시간 38분 정지를 놓침)
        "INSERT INTO collector_runs (collector, run_at, status, rows, message) "
        "VALUES ('watchdog', ?, 'alert', 0, ?)",
        (datetime.now().isoformat(timespec="seconds"), kind),
    )
    con.commit()
    return True


def _restart_task(name: str) -> str:
    """Windows 작업 스케줄러 작업을 재기동. 반환: 결과 요약 문자열.

    감지만 하고 되살리지 않으면 무인 가동에서 정지가 그대로 누적된다 —
    2026-07-28 실사고: 엔진이 05:51에 죽고 07:05에 경보가 갔지만 아무도 재시작하지 않아
    **2시간 38분간 자동매매가 멈춰 있었다**(사용자가 아침에 발견).
    """
    import subprocess

    try:
        subprocess.run(["schtasks", "/End", "/TN", name], capture_output=True, timeout=20)
        r = subprocess.run(["schtasks", "/Run", "/TN", name], capture_output=True, timeout=20)
        return "재시작 성공" if r.returncode == 0 else f"재시작 실패(rc={r.returncode})"
    except Exception as e:
        return f"재시작 시도 실패({type(e).__name__})"


def _death_note(con) -> str:
    """정지의 성격 판별 — 정상 종료였나 급사였나. 원인 추적의 출발점.

    워커는 정상 종료 시 collector_runs에 'stop'을 남기고, 매 폴마다 생존 파일을 갱신한다.
    → **stop 기록이 없는데 생존 파일이 끊겼으면 급사**(2026-07-28 사례가 이 유형).
    """
    try:
        from src.trading.worker import last_alive

        alive = last_alive()
    except Exception:
        alive = None
    try:
        r = con.execute(
            "SELECT run_at FROM collector_runs WHERE collector='engine' AND status='stop' "
            "ORDER BY run_at DESC LIMIT 1").fetchone()
        stop_at = r["run_at"] if r else None
    except Exception:
        stop_at = None
    if stop_at and alive and stop_at >= alive:
        return f"정상 종료({stop_at[11:16]}) 후 미기동"
    return f"**급사**(마지막 생존 {alive[11:19] if alive else '기록 없음'} · 종료 기록 없음)"


def check_engine(con) -> int:
    """hourly에서 호출 — 엔진 워커 생존 확인 → **자동 재시작 후** 경보. 경보 발송 시 1."""
    m = float(os.getenv("ENGINE_STALL_MIN", "45"))
    if not _stalled(con, "engine", m):
        return 0
    note = _death_note(con)                    # 재시작 전에 판별해야 흔적이 안 덮인다
    res = _restart_task(os.getenv("ENGINE_TASK_NAME", "market-hub-engine"))
    return int(_alert_once(
        con, "engine_stall",
        f"🚨 워치독: 엔진 워커 하트비트가 {int(m)}분째 없음 → 자동 {res}\n"
        f"정지 성격: {note}\n"
        f"다음 hourly(1시간 뒤)에도 경보가 오면 수동 확인 필요"))


def check_hourly(con) -> int:
    """엔진 워커에서 호출 — 시간별 수집 생존 확인. 경보 발송 시 1."""
    m = float(os.getenv("HOURLY_STALL_MIN", "150"))
    if _stalled(con, "sentiment", m):
        return int(_alert_once(
            con, "hourly_stall",
            f"🚨 워치독: 시간별 수집이 {int(m)}분째 없음 — market-hub-hourly 확인 필요"))
    return 0
