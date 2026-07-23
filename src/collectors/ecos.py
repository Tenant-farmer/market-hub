"""한국은행 ECOS 거시 수집 — 기준금리·국고채 3/10년(일별)·CPI(월별 지수).

- ECOS_API_KEY 없으면 조용히 건너뜀 (키 게이트)
- prices_daily(market='MACRO', symbol='ECOS:*')로 저장 → 매크로 카드·브리핑 파이프 재사용
- 첫 수집 시 자동 백필 (일별 400일 / CPI 5년 — YoY 계산 여유)
- CPI는 지수 레벨 저장(날짜=해당월 1일), 전년비는 조회부에서 계산
"""
import os
from datetime import datetime, timedelta

import requests

from src import db
from src.collectors.yf_util import PRICE_COLS

BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
# (심볼, 통계표, 주기, 항목코드)
SERIES = [
    ("ECOS:BASE",   "722Y001", "D", "0101000"),    # 한국은행 기준금리
    ("ECOS:KTB3Y",  "817Y002", "D", "010200000"),  # 국고채 3년
    ("ECOS:KTB10Y", "817Y002", "D", "010210000"),  # 국고채 10년
    ("ECOS:CPI",    "901Y009", "M", "0"),          # 소비자물가 총지수 (월)
]


def _fetch(key, stat, cycle, item, start, end) -> list[dict]:
    url = f"{BASE}/{key}/json/kr/1/1000/{stat}/{cycle}/{start}/{end}/{item}"
    d = requests.get(url, timeout=30).json()
    return d.get("StatisticSearch", {}).get("row", [])


def collect(con, days: int = 30) -> int:
    key = os.getenv("ECOS_API_KEY")
    if not key:
        return 0
    first = con.execute(
        "SELECT COUNT(*) c FROM prices_daily WHERE symbol LIKE 'ECOS:%'"
    ).fetchone()["c"] == 0
    if first:
        days = 400
    now = datetime.now()
    rows = []
    for sym, stat, cycle, item in SERIES:
        if cycle == "D":
            start = (now - timedelta(days=days)).strftime("%Y%m%d")
            end = now.strftime("%Y%m%d")
        else:                                       # 월별 (CPI)
            months = 60 if first else 15
            start = (now - timedelta(days=months * 31)).strftime("%Y%m")
            end = now.strftime("%Y%m")
        for r in _fetch(key, stat, cycle, item, start, end):
            t, v = r.get("TIME", ""), r.get("DATA_VALUE")
            if not v:
                continue
            dt = f"{t[:4]}-{t[4:6]}-{t[6:]}" if cycle == "D" else f"{t[:4]}-{t[4:6]}-01"
            val = float(v)
            rows.append((sym, "MACRO", dt, val, val, val, val, 0, 0))
    return db.upsert(con, "prices_daily", PRICE_COLS, rows)


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    sys.path.insert(0, ".")

    c = db.connect()
    print("적재:", collect(c))
    for sym, _, _, _ in SERIES:
        r = c.execute("SELECT date, close FROM prices_daily WHERE symbol=? "
                      "ORDER BY date DESC LIMIT 1", (sym,)).fetchone()
        print(f"  {sym}: {r['date']} = {r['close']}" if r else f"  {sym}: 없음")
    c.close()
