"""수집기 공통: 실행 기록(collector_runs) + 실패 격리.

수집기 하나가 죽어도 나머지는 계속 돌게 하고, 실패 이력은
collector_runs에 남겨 대시보드 신선도 배지의 근거로 쓴다.
"""
import traceback
from datetime import datetime

from src import db


def run_collector(name: str, fn) -> int:
    """fn(con) -> 적재 row 수. 예외는 잡아서 기록하고 -1 반환."""
    con = db.connect()
    try:
        rows = fn(con)
        status, msg = "ok", None
    except Exception:
        rows, status, msg = -1, "error", traceback.format_exc(limit=3)
    con.execute(
        "INSERT INTO collector_runs (collector, run_at, status, rows, message) VALUES (?,?,?,?,?)",
        (name, datetime.now().isoformat(timespec="seconds"), status, max(rows, 0), msg),
    )
    con.commit()
    con.close()
    if status == "error":
        print(f"[{name}] ERROR\n{msg}")
    else:
        print(f"[{name}] ok: {rows} rows")
    return rows
