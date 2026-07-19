"""연방기금 목표금리(상한) 수집 — FRED 무키 CSV (DFEDTARU).

prices_daily(symbol='DFEDTARU', market='MACRO')에 저장해 기존 인프라 재사용.
"""
import csv
import io

import requests

from src import db

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFEDTARU"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
COLS = ["symbol", "market", "date", "open", "high", "low", "close", "volume", "value"]


def collect(con, start: str = "2020-01-01") -> int:
    r = requests.get(URL, headers=UA, timeout=30)
    r.raise_for_status()
    rows = []
    for rec in csv.DictReader(io.StringIO(r.text)):
        d = rec.get("observation_date") or rec.get("DATE") or ""
        v = rec.get("DFEDTARU") or ""
        if d < start or not v or v == ".":
            continue
        rate = float(v)
        rows.append(("DFEDTARU", "MACRO", d, rate, rate, rate, rate, 0, 0))
    return db.upsert(con, "prices_daily", COLS, rows)
