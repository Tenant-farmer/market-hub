"""SQLite 연결/헬퍼. DB 파일: <repo>/data/market.db

MARKET_HUB_DB 환경변수로 경로를 바꿀 수 있다 — 백업 복구 리허설(운영 DB를 건드리지 않고
복구본을 실제로 띄워보기)과 VPS 이관 시 필요. 미설정이면 기존 경로 그대로.
"""
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("MARKET_HUB_DB") or (ROOT / "data" / "market.db"))
SCHEMA_PATH = ROOT / "db" / "schema.sql"


def connect() -> sqlite3.Connection:
    # 경로는 호출 시점에 다시 읽는다 — 리허설이 프로세스 중간에 환경변수를 바꿔도 반영되도록
    path = Path(os.getenv("MARKET_HUB_DB") or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    # 동시 쓰기(워커+수집기) 시 잠금 대기. 5초는 짧았다 — hourly가 kr_stocks 5,301행 등을
    # 쏟아붓는 :05 구간에서 워커 기록이 실제로 5초를 넘겨 실패했다(2026-07-28 12:05 실측).
    # 기다렸다 쓰는 편이 기록을 잃는 것보다 낫다. 폴은 15초라 최악에도 회차가 밀릴 뿐이다.
    con.execute(f"PRAGMA busy_timeout={os.getenv('SQLITE_BUSY_MS', '30000')}")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    con.commit()


def upsert(con: sqlite3.Connection, table: str, columns: list[str], rows: list[tuple]) -> int:
    """INSERT OR REPLACE 일괄 적재. 반환: 적재 row 수."""
    if not rows:
        return 0
    ph = ",".join("?" * len(columns))
    con.executemany(
        f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({ph})", rows
    )
    con.commit()
    return len(rows)
