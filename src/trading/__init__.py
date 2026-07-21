"""자동매매 파이프라인 — 웹훅 수신 → signals 큐 → 엔진 → 브로커 어댑터.

paper_log 단계: 브로커 키 없이 전체 배관(수신·검증·멱등·기록)을 검증한다.
"""


def ensure_tables(con) -> None:
    """signals/orders 테이블 보장 (기존 DB에도 자기치유)."""
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
