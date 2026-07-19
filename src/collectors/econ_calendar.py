"""경제지표 캘린더 수집 (Nasdaq 공개 API) — 미국·한국 이벤트만 저장.

미래 날짜는 임박해야 채워지는 구조라 매일 갱신 (오늘~+7일 창을 통째로 교체).
"""
import time
from datetime import date, timedelta

import requests

from src import db

URL = "https://api.nasdaq.com/api/calendar/economicevents"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
}
COUNTRY = {"United States": "US", "South Korea": "KR"}
MAJOR_KW = ("CPI", "PPI", "GDP", "Nonfarm", "Unemployment", "FOMC", "Interest Rate",
            "Retail Sales", "PCE", "ISM", "Consumer Confidence", "Michigan", "Payroll")
COLS = ["date", "gmt", "country", "event", "actual", "consensus", "previous", "major"]


def collect(con, days: int = 8) -> int:
    con.execute(
        "CREATE TABLE IF NOT EXISTS econ_calendar (date TEXT NOT NULL, gmt TEXT, country TEXT, "
        "event TEXT, actual TEXT, consensus TEXT, previous TEXT, major INTEGER DEFAULT 0)"
    )
    today = date.today()
    con.execute("DELETE FROM econ_calendar WHERE date >= ?", (today.isoformat(),))
    con.execute("DELETE FROM econ_calendar WHERE date < ?", ((today - timedelta(days=3)).isoformat(),))
    con.commit()

    rows = []
    for i in range(days):
        d = today + timedelta(days=i)
        try:
            r = requests.get(URL, params={"date": d.isoformat()}, headers=UA, timeout=20)
            data = (r.json().get("data") or {}).get("rows") or []
        except Exception:
            continue
        for row in data:
            cc = COUNTRY.get((row.get("country") or "").strip())
            if not cc:
                continue
            name = (row.get("eventName") or "").strip()
            major = int(any(k.lower() in name.lower() for k in MAJOR_KW))
            rows.append((
                d.isoformat(), row.get("gmt") or "", cc, name,
                row.get("actual") or "", row.get("consensus") or "",
                row.get("previous") or "", major,
            ))
        time.sleep(0.4)

    if rows:
        con.executemany(
            f"INSERT INTO econ_calendar ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
            rows,
        )
        con.commit()
    return len(rows)
