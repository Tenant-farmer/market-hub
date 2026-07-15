"""미국 개별종목(S&P500) 수집.

유니버스: 위키피디아 S&P500 구성종목 표 (티커, 종목명, GICS 섹터)
시세: yfinance 100종목씩 배치
"""
import io
from datetime import date

import pandas as pd
import requests

from src import config, db
from src.collectors.yf_util import PRICE_COLS, fetch_daily_rows

WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# GICS 섹터 → 우리가 쓰는 SPDR ETF 코드 (섹터 그룹 키)
GICS_TO_ETF = {
    "Information Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}

MAP_COLS = ["stock_code", "market", "sector_code", "sector_name", "name", "as_of"]


def _ensure_name_col(con):
    cols = [r["name"] for r in con.execute("PRAGMA table_info(sector_map)")]
    if "name" not in cols:
        con.execute("ALTER TABLE sector_map ADD COLUMN name TEXT")


def refresh_universe(con) -> int:
    """위키피디아에서 S&P500 구성종목을 받아 sector_map 갱신."""
    _ensure_name_col(con)
    names = config.load()["us"].get("names", {})
    html = requests.get(WIKI, headers=UA, timeout=30).text
    table = pd.read_html(io.StringIO(html))[0]
    rows = []
    for _, r in table.iterrows():
        etf = GICS_TO_ETF.get(str(r["GICS Sector"]).strip())
        if not etf:
            continue
        tkr = str(r["Symbol"]).strip().replace(".", "-")  # BRK.B → BRK-B (야후 표기)
        rows.append((tkr, "US_STOCK", etf, names.get(etf, etf), str(r["Security"]), date.today().isoformat()))
    return db.upsert(con, "sector_map", MAP_COLS, rows)


def refresh_market_caps(con) -> int:
    """시가총액 갱신 — tradingview-screener (비공식, 실패 허용).

    미국 시총 상위 ~1600개를 한 번의 요청으로 받아 S&P500 티커와 매칭.
    """
    from tradingview_screener import Query

    con.execute(
        "CREATE TABLE IF NOT EXISTS stock_meta (symbol TEXT PRIMARY KEY, mcap REAL, as_of TEXT)"
    )
    _, df = (
        Query()
        .select("name", "market_cap_basic")
        .order_by("market_cap_basic", ascending=False)
        .limit(1600)
        .get_scanner_data()
    )
    known = {
        r["stock_code"]
        for r in con.execute("SELECT stock_code FROM sector_map WHERE market='US_STOCK'")
    }
    today = date.today().isoformat()
    rows = []
    for _, r in df.iterrows():
        sym = str(r["name"]).replace(".", "-")  # TV 'BRK.B' → 우리 표기 'BRK-B'
        if sym in known and pd.notna(r["market_cap_basic"]):
            rows.append((sym, float(r["market_cap_basic"]), today))
    return db.upsert(con, "stock_meta", ["symbol", "mcap", "as_of"], rows)


def collect(con, days: int = 7) -> int:
    n_univ = refresh_universe(con)
    tickers = [
        r["stock_code"]
        for r in con.execute("SELECT stock_code FROM sector_map WHERE market='US_STOCK'")
    ]
    rows = fetch_daily_rows(tickers, days, market_for=lambda s: "US_STOCK")
    print(f"[us_stocks] universe {n_univ}, price rows {len(rows)}")
    n = db.upsert(con, "prices_daily", PRICE_COLS, rows)
    try:
        m = refresh_market_caps(con)
        print(f"[us_stocks] market caps {m}")
    except Exception as e:  # 비공식 소스 — 죽어도 시세 수집엔 영향 없음
        print(f"[us_stocks] market caps skip: {e}")
    return n
