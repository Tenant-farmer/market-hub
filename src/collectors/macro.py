"""시장 컨텍스트(매크로) 지표 수집 — WTI, 금, 미국채 10Y/3M, HYG (yfinance).

^VIX는 us_sectors가 US_INDEX로 이미 수집하므로 여기서 제외 (PK 충돌 방지).
"""
from src import config, db
from src.collectors.yf_util import PRICE_COLS, fetch_daily_rows


def collect(con, days: int = 7) -> int:
    symbols = config.load()["macro"]["symbols"]
    rows = fetch_daily_rows(symbols, days, market_for=lambda s: "MACRO")
    return db.upsert(con, "prices_daily", PRICE_COLS, rows)
