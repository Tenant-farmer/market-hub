"""미국 실적 캘린더 수집 (Nasdaq 공개 API) — S&P500 유니버스만 저장.

향후 N일을 하루 단위로 조회 (요청당 0.4초 딜레이), 지난 일정은 정리.
"""
import time
from datetime import date, timedelta

import requests

from src import db

URL = "https://api.nasdaq.com/api/calendar/earnings"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
}
COLS = ["symbol", "date", "when_time", "name", "eps_forecast"]


def collect(con, days: int = 14) -> int:
    con.execute(
        "CREATE TABLE IF NOT EXISTS earnings_calendar (symbol TEXT NOT NULL, date TEXT NOT NULL, "
        "when_time TEXT, name TEXT, eps_forecast TEXT, PRIMARY KEY (symbol, date))"
    )
    universe = {
        r["stock_code"]
        for r in con.execute("SELECT stock_code FROM sector_map WHERE market='US_STOCK'")
    }
    today = date.today()
    con.execute("DELETE FROM earnings_calendar WHERE date < ?", (today.isoformat(),))
    con.commit()

    rows = []
    for i in range(days):
        d = today + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        try:
            r = requests.get(URL, params={"date": d.isoformat()}, headers=UA, timeout=20)
            data = (r.json().get("data") or {}).get("rows") or []
        except Exception:
            continue
        for row in data:
            sym = (row.get("symbol") or "").strip()
            if sym in universe:
                rows.append((
                    sym, d.isoformat(), row.get("time") or "",
                    row.get("name") or "", row.get("epsForecast") or "",
                ))
        time.sleep(0.4)
    return db.upsert(con, "earnings_calendar", COLS, rows)
