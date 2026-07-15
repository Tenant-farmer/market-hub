"""SQLite 연결/헬퍼. DB 파일: <repo>/data/market.db"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "market.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
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
