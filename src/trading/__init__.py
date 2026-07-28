"""자동매매 파이프라인 — 웹훅 수신 → signals 큐 → 엔진 → 브로커 어댑터.

paper_log 단계: 브로커 키 없이 전체 배관(수신·검증·멱등·기록)을 검증한다.
"""


_READY: set[str] = set()      # 이미 보장을 마친 DB 파일 (프로세스 단위)


def _db_file(con) -> str:
    """연결이 붙은 DB 파일 경로. :memory:면 빈 문자열."""
    try:
        for _seq, name, file in con.execute("PRAGMA database_list"):
            if name == "main":
                return file or ""
    except Exception:
        pass
    return ""


def ensure_tables(con) -> None:
    """signals/orders 테이블 보장 (기존 DB에도 자기치유).

    **파일 DB는 프로세스당 1회만** 실제로 수행한다. 이 함수는 다 갖춰진 DB에서도
    `INSERT OR IGNORE` 때문에 **쓰기 트랜잭션을 연다**(실측 확인). 워커가 매 폴(15초)마다
    호출하니 하루 5,760번 쓰기 락을 요구했고, hourly 수집이 락을 쥔 시간대마다
    `database is locked`로 그 회차가 통째로 날아갔다(2026-07-28 기준 하루 6~7건).
    스키마는 프로세스 도중에 사라지지 않으므로 한 번이면 충분하다.
    :memory:는 캐시하지 않는다 — 테스트가 연결마다 새 DB를 쓰기 때문.
    """
    key = _db_file(con)
    if key and key in _READY:
        return
    con.execute(
        "CREATE TABLE IF NOT EXISTS signals ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE, received_at TEXT NOT NULL, "
        "source TEXT, ticker TEXT, action TEXT, qty REAL, price REAL, strategy TEXT, raw TEXT, "
        "status TEXT DEFAULT 'new', processed_at TEXT, result TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS orders ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER, client_order_id TEXT UNIQUE, "
        "broker TEXT, ticker TEXT, action TEXT, qty REAL, price REAL, status TEXT, "
        "created_at TEXT, message TEXT)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS trading_state ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), mode TEXT NOT NULL DEFAULT 'paper', "
        "armed INTEGER NOT NULL DEFAULT 0, updated_at TEXT)"
    )
    con.execute("INSERT OR IGNORE INTO trading_state (id, mode, armed) VALUES (1, 'paper', 0)")
    con.commit()
    if key:
        _READY.add(key)
