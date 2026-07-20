"""US 섹터별 CapEx (설비투자) — 섹터 시총 상위 종목의 분기 현금흐름 합산.

분기 공시 데이터라 월 1회면 충분 (hourly 아침 슬롯에서 25일 경과 시 재수집).
금융·리츠는 CapEx 개념이 안 맞아 제외 (야후 현금흐름에 항목 자체가 없음 — JPM 확인).
종목별로 최신 5개 분기를 저장해 TTM 합산과 최신분기 YoY(전년 동분기 대비)를 만든다.
"""
import time
from datetime import datetime

import yfinance as yf

SKIP_SECTORS = {"금융", "리츠"}
TOP_N = 10


def fetch_capex(ticker: str):
    """야후 분기 현금흐름에서 CapEx 추출 → (최신분기말, TTM, 최신분기, 전년동분기) 또는 None."""
    cf = yf.Ticker(ticker).quarterly_cashflow
    idx = [i for i in cf.index if "Capital Expenditure" in str(i)]
    if not idx:
        return None
    s = cf.loc[idx[0]].dropna().sort_index(ascending=False)
    if len(s) < 4:
        return None
    vals = [abs(float(v)) for v in s.iloc[:5]]
    return (
        str(s.index[0].date()), sum(vals[:4]), vals[0],
        vals[4] if len(vals) >= 5 else None,
    )


def collect(con, top_n: int = TOP_N) -> int:
    con.execute(
        "CREATE TABLE IF NOT EXISTS us_capex ("
        "sector TEXT NOT NULL, symbol TEXT NOT NULL, latest_q TEXT, "
        "capex_ttm REAL, q_latest REAL, q_yoy_base REAL, fetched_at TEXT, "
        "PRIMARY KEY (sector, symbol))"
    )
    rows = con.execute(
        """
        SELECT m.sector_name AS sector, m.stock_code AS sym
        FROM sector_map m JOIN stock_meta s ON s.symbol = m.stock_code
        WHERE m.market='US_STOCK' AND s.mcap IS NOT NULL
        ORDER BY m.sector_name, s.mcap DESC
        """
    ).fetchall()
    by_sec: dict[str, list[str]] = {}
    for r in rows:
        if r["sector"] in SKIP_SECTORS:
            continue
        picks = by_sec.setdefault(r["sector"], [])
        if len(picks) < top_n:
            picks.append(r["sym"])

    fetched = datetime.now().isoformat(timespec="seconds")
    out = []
    for sector, syms in by_sec.items():
        for sym in syms:
            try:
                got = fetch_capex(sym)
                if got:
                    out.append((sector, sym, *got, fetched))
            except Exception:
                continue
            time.sleep(0.2)
    if out:
        con.execute("DELETE FROM us_capex")
        con.executemany("INSERT OR REPLACE INTO us_capex VALUES (?,?,?,?,?,?,?)", out)
        con.commit()
    return len(out)
