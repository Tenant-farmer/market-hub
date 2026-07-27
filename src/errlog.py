"""조용한 실패 방지 — 삼킨 예외를 기록하는 공용 헬퍼.

배경(2026-07-27 실사고 2건):
- trade_alerts가 SQL 컬럼 누락으로 IndexError → `except: pass`가 삼켜 **알림 0건**을 몇 시간 방치
- 캔들 백테스트 벤치마크 버그도 예외는 아니었지만 같은 계열(조용한 오작동)

원칙: 예외를 삼켜야 하는 자리(부가 기능이라 죽으면 안 되는 곳)는 **삼키되 흔적을 남긴다.**
사용:
    from src.errlog import swallow
    try:
        ...
    except Exception as e:
        swallow("trade_alerts.notify", e)      # 로그 + collector_runs 기록, 흐름은 계속

로그: data/errors.log (파일) + collector_runs(collector='swallowed') — /health에서 확인 가능.
같은 위치의 반복 예외는 10분에 1회만 기록(로그 폭주 방지).
"""
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "errors.log"
_LAST: dict = {}                       # {where: last_epoch} — 중복 억제
_COOLDOWN = 600


def swallow(where: str, exc: BaseException, *, db_record: bool = True) -> None:
    """예외를 삼키되 기록한다. 절대 재발생시키지 않는다(호출부 흐름 보존)."""
    now = time.time()
    if now - _LAST.get(where, 0) < _COOLDOWN:
        return
    _LAST[where] = now
    msg = f"{type(exc).__name__}: {str(exc)[:200]}"
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {where} — {msg}"
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n" + "".join(traceback.format_exception_only(type(exc), exc)))
    except Exception:
        pass
    if db_record:
        try:
            from src import db

            con = db.connect()
            con.execute(
                "INSERT INTO collector_runs (collector, run_at, status, rows, message) "
                "VALUES ('swallowed', ?, 'error', 0, ?)",
                (datetime.now().isoformat(timespec="seconds"), f"{where} | {msg}"[:200]))
            con.commit()
            con.close()
        except Exception:
            pass


def recent(con, hours: int = 24) -> list[dict]:
    """최근 삼킨 예외 목록 (/health 표시용)."""
    try:
        rows = con.execute(
            "SELECT run_at, message FROM collector_runs WHERE collector='swallowed' "
            "AND run_at >= datetime('now','localtime',?) ORDER BY run_at DESC LIMIT 20",
            (f"-{hours} hours",)).fetchall()
        return [{"at": r["run_at"][5:16], "msg": r["message"]} for r in rows]
    except Exception:
        return []
